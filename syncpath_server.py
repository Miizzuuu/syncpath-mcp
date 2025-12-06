#!/usr/bin/env python3

import json
import logging
import os
import sys
from datetime import datetime, timezone
from uuid import uuid4

import psycopg2
from mcp.server.fastmcp import FastMCP
from psycopg2.extras import RealDictCursor

# Configure logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("syncpath-server")

# Initialize MCP server
mcp = FastMCP("syncpath")

# Configuration
DATABASE_URL = os.environ.get("SYNCPATH_DATABASE_URL", "")
USER_ID = os.environ.get("SYNCPATH_USER_ID", "")


def get_db_connection():
    """Create a database connection."""
    if not DATABASE_URL:
        raise ValueError("SYNCPATH_DATABASE_URL not configured")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def format_project(project):
    """Format a project dict for display."""
    created = project.get("created_at", "")
    updated = project.get("updated_at", "")
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    if hasattr(updated, "isoformat"):
        updated = updated.isoformat()
    return f"- **{project['name']}** (ID: `{project['id']}`)\n  Created: {created} | Updated: {updated}"


def format_task(t, name_lookup=None):
    """Format a task dict for display."""
    start = t.get("start_date", "N/A")
    end = t.get("end_date", "N/A")
    if hasattr(start, "isoformat"):
        start = start.isoformat()
    if hasattr(end, "isoformat"):
        end = end.isoformat()

    # Format dependencies with names if lookup available
    deps = t.get("dependencies", []) or []
    if deps and name_lookup:
        deps_str = ", ".join(
            [
                f"{name_lookup.get(d.get('taskId'), d.get('taskId'))} ({d.get('type')})"
                for d in deps
            ]
        )
    elif deps:
        deps_str = ", ".join([f"{d.get('taskId')} ({d.get('type')})" for d in deps])
    else:
        deps_str = "None"

    # Format parent with name if lookup available
    parent_id = t.get("parent_id")
    if parent_id and name_lookup:
        parent_str = f"{name_lookup.get(parent_id, parent_id)}"
    else:
        parent_str = parent_id or "None"

    return f"""- **{t["name"]}** (ID: `{t["id"]}`)
  Type: {t["type"]} | Status: {t["status"]} | Progress: {t["percentage"]}%
  Start: {start} | End: {end}
  Assignee: {t.get("assignee") or "Unassigned"} | Cost: {t.get("cost", 0)}
  Parent: {parent_str} | Dependencies: {deps_str}"""


def get_task_name_lookup(cur, project_id):
    """Build a task ID to name lookup dict for a project."""
    cur.execute("SELECT id, name FROM task WHERE project_id = %s", (project_id,))
    tasks = cur.fetchall()
    return {t["id"]: t["name"] for t in tasks}


def resolve_task_reference(cur, project_id, reference):
    """Resolve a task reference (name or ID) to a task ID. Returns None if not found."""
    if not reference or not reference.strip():
        return None

    reference = reference.strip()

    # Check if it's already a UUID (36 chars with 4 dashes)
    if len(reference) == 36 and reference.count("-") == 4:
        # Verify it exists
        cur.execute(
            "SELECT id FROM task WHERE id = %s AND project_id = %s",
            (reference, project_id),
        )
        if cur.fetchone():
            return reference

    # Try to find by name
    cur.execute(
        "SELECT id FROM task WHERE name = %s AND project_id = %s",
        (reference, project_id),
    )
    result = cur.fetchone()
    if result:
        return result["id"]

    return None


def resolve_dependencies(cur, project_id, dependencies_json):
    """Resolve dependencies JSON, converting task names to IDs. Returns resolved JSON array."""
    if not dependencies_json or not dependencies_json.strip():
        return []

    try:
        deps = (
            json.loads(dependencies_json)
            if isinstance(dependencies_json, str)
            else dependencies_json
        )
    except json.JSONDecodeError:
        return []

    if not isinstance(deps, list):
        return []

    resolved = []
    for dep in deps:
        if not isinstance(dep, dict):
            continue

        task_ref = dep.get("taskId", dep.get("task_id", ""))
        dep_type = dep.get("type", "FS")

        if dep_type not in ["FS", "FF", "SS", "SF"]:
            dep_type = "FS"

        resolved_id = resolve_task_reference(cur, project_id, task_ref)
        if resolved_id:
            resolved.append({"taskId": resolved_id, "type": dep_type})

    return resolved


# === PROJECT TOOLS ===
@mcp.tool()
async def list_projects() -> str:
    """List all projects for the authenticated user."""
    logger.info("Listing projects")

    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM project WHERE user_id = %s ORDER BY updated_at DESC",
            (USER_ID,),
        )
        projects = cur.fetchall()
        cur.close()
        conn.close()

        if not projects:
            return "📁 No projects found. Create one with `create_project`."

        result = f"📊 Found {len(projects)} project(s):\n\n"
        for p in projects:
            result += format_project(p) + "\n\n"
        return result
    except Exception as e:
        logger.error(f"Error listing projects: {e}")
        return f"❌ Error: {str(e)}"


@mcp.tool()
async def get_project(project_id: str = "") -> str:
    """Get details of a specific project by ID."""
    logger.info(f"Getting project {project_id}")

    if not project_id.strip():
        return "❌ Error: project_id is required"
    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM project WHERE id = %s AND user_id = %s",
            (project_id, USER_ID),
        )
        project = cur.fetchone()
        cur.close()
        conn.close()

        if not project:
            return f"❌ Project not found: {project_id}"

        return f"📁 Project Details:\n\n{format_project(project)}"
    except Exception as e:
        logger.error(f"Error getting project: {e}")
        return f"❌ Error: {str(e)}"


@mcp.tool()
async def create_project(name: str = "") -> str:
    """Create a new project with the given name."""
    logger.info(f"Creating project: {name}")

    if not name.strip():
        return "❌ Error: name is required"
    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    try:
        project_id = str(uuid4())
        now = datetime.now(timezone.utc)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO project (id, name, user_id, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (project_id, name.strip(), USER_ID, now, now),
        )
        project = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        return f"✅ Project created successfully!\n\n{format_project(project)}"
    except Exception as e:
        logger.error(f"Error creating project: {e}")
        return f"❌ Error: {str(e)}"


@mcp.tool()
async def update_project(project_id: str = "", name: str = "") -> str:
    """Update a project's name."""
    logger.info(f"Updating project {project_id}")

    if not project_id.strip():
        return "❌ Error: project_id is required"
    if not name.strip():
        return "❌ Error: name is required"
    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    try:
        now = datetime.now(timezone.utc)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """UPDATE project SET name = %s, updated_at = %s
               WHERE id = %s AND user_id = %s RETURNING *""",
            (name.strip(), now, project_id, USER_ID),
        )
        project = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not project:
            return f"❌ Project not found or not authorized: {project_id}"

        return f"✅ Project updated successfully!\n\n{format_project(project)}"
    except Exception as e:
        logger.error(f"Error updating project: {e}")
        return f"❌ Error: {str(e)}"


@mcp.tool()
async def delete_project(project_id: str = "") -> str:
    """Delete a project and all its tasks (cascade delete)."""
    logger.info(f"Deleting project {project_id}")

    if not project_id.strip():
        return "❌ Error: project_id is required"
    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM project WHERE id = %s AND user_id = %s RETURNING id, name",
            (project_id, USER_ID),
        )
        deleted = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not deleted:
            return f"❌ Project not found or not authorized: {project_id}"

        return f"✅ Project '{deleted['name']}' deleted successfully (including all tasks)."
    except Exception as e:
        logger.error(f"Error deleting project: {e}")
        return f"❌ Error: {str(e)}"


# === TASK TOOLS ===
@mcp.tool()
async def list_tasks(project_id: str = "", show_hierarchy: str = "true") -> str:
    """List all tasks for a specific project. Set show_hierarchy to 'true' to display nested structure."""
    logger.info(f"Listing tasks for project {project_id}")

    if not project_id.strip():
        return "❌ Error: project_id is required"
    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Verify project ownership
        cur.execute(
            "SELECT id FROM project WHERE id = %s AND user_id = %s",
            (project_id, USER_ID),
        )
        if not cur.fetchone():
            cur.close()
            conn.close()
            return f"❌ Project not found or not authorized: {project_id}"

        cur.execute(
            "SELECT * FROM task WHERE project_id = %s ORDER BY created_at ASC",
            (project_id,),
        )
        tasks = cur.fetchall()

        # Build name lookup
        name_lookup = {t["id"]: t["name"] for t in tasks}

        cur.close()
        conn.close()

        if not tasks:
            return f"📋 No tasks found in project. Create one with `create_task` or `bulk_create_tasks`."

        if show_hierarchy.lower() == "true":
            # Build hierarchical display
            result = f"📋 Found {len(tasks)} task(s) in project:\n\n"

            # Separate root tasks and child tasks
            root_tasks = [t for t in tasks if not t.get("parent_id")]
            children_map = {}
            for t in tasks:
                parent = t.get("parent_id")
                if parent:
                    if parent not in children_map:
                        children_map[parent] = []
                    children_map[parent].append(t)

            def format_task_tree(task, indent=0):
                prefix = "  " * indent
                deps = task.get("dependencies", []) or []
                deps_str = (
                    ", ".join(
                        [
                            f"{name_lookup.get(d.get('taskId'), d.get('taskId'))} ({d.get('type')})"
                            for d in deps
                        ]
                    )
                    if deps
                    else "None"
                )

                start = task.get("start_date", "N/A")
                end = task.get("end_date", "N/A")
                if hasattr(start, "strftime"):
                    start = start.strftime("%Y-%m-%d")
                if hasattr(end, "strftime"):
                    end = end.strftime("%Y-%m-%d")

                output = f"{prefix}📌 **{task['name']}** (`{task['id']}`)\n"
                output += f"{prefix}   Type: {task['type']} | Status: {task['status']} | Progress: {task['percentage']}%\n"
                output += f"{prefix}   Dates: {start} → {end} | Assignee: {task.get('assignee') or 'Unassigned'}\n"
                output += f"{prefix}   Cost: {task.get('cost', 0)} | Dependencies: {deps_str}\n"

                # Recursively add children
                if task["id"] in children_map:
                    for child in children_map[task["id"]]:
                        output += format_task_tree(child, indent + 1)

                return output

            for task in root_tasks:
                result += format_task_tree(task) + "\n"

            return result
        else:
            result = f"📋 Found {len(tasks)} task(s):\n\n"
            for t in tasks:
                result += format_task(t, name_lookup) + "\n\n"
            return result
    except Exception as e:
        logger.error(f"Error listing tasks: {e}")
        return f"❌ Error: {str(e)}"


@mcp.tool()
async def get_task(task_id: str = "", project_id: str = "") -> str:
    """Get details of a specific task by ID or name."""
    logger.info(f"Getting task {task_id}")

    if not task_id.strip():
        return "❌ Error: task_id is required"
    if not project_id.strip():
        return "❌ Error: project_id is required"
    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Verify project ownership
        cur.execute(
            "SELECT id FROM project WHERE id = %s AND user_id = %s",
            (project_id, USER_ID),
        )
        if not cur.fetchone():
            cur.close()
            conn.close()
            return f"❌ Project not found or not authorized: {project_id}"

        # Resolve task reference (could be name or ID)
        resolved_id = resolve_task_reference(cur, project_id, task_id)
        if not resolved_id:
            cur.close()
            conn.close()
            return f"❌ Task not found: {task_id}"

        cur.execute(
            "SELECT * FROM task WHERE id = %s AND project_id = %s",
            (resolved_id, project_id),
        )
        t = cur.fetchone()

        # Get name lookup for better display
        name_lookup = get_task_name_lookup(cur, project_id)

        # Get children
        cur.execute("SELECT id, name FROM task WHERE parent_id = %s", (resolved_id,))
        children = cur.fetchall()

        cur.close()
        conn.close()

        if not t:
            return f"❌ Task not found: {task_id}"

        result = f"📋 Task Details:\n\n{format_task(t, name_lookup)}"

        if children:
            result += f"\n\n**Children ({len(children)}):**\n"
            for child in children:
                result += f"  - {child['name']} (`{child['id']}`)\n"

        return result
    except Exception as e:
        logger.error(f"Error getting task: {e}")
        return f"❌ Error: {str(e)}"


@mcp.tool()
async def create_task(
    project_id: str = "",
    name: str = "",
    task_type: str = "task",
    status: str = "pending",
    start_date: str = "",
    end_date: str = "",
    assignee: str = "",
    percentage: str = "0",
    cost: str = "0",
    parent_id: str = "",
    dependencies_json: str = "[]",
) -> str:
    """Create a new task. parent_id can be task name or UUID. dependencies_json: array of {taskId (name or UUID), type: FS|FF|SS|SF}."""
    logger.info(f"Creating task in project {project_id}: {name}")

    if not project_id.strip():
        return "❌ Error: project_id is required"
    if not name.strip():
        return "❌ Error: name is required"
    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    valid_types = ["task", "group", "milestone"]
    valid_statuses = ["pending", "in-progress", "completed", "blocked"]

    if task_type not in valid_types:
        return f"❌ Error: task_type must be one of: {', '.join(valid_types)}"
    if status not in valid_statuses:
        return f"❌ Error: status must be one of: {', '.join(valid_statuses)}"

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Verify project ownership
        cur.execute(
            "SELECT id FROM project WHERE id = %s AND user_id = %s",
            (project_id, USER_ID),
        )
        if not cur.fetchone():
            cur.close()
            conn.close()
            return f"❌ Project not found or not authorized: {project_id}"

        task_id = str(uuid4())
        now = datetime.now(timezone.utc)

        # Parse dates
        start_dt = None
        end_dt = None
        if start_date.strip():
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        if end_date.strip():
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))

        # Parse percentage and cost
        pct = int(percentage) if percentage.strip() else 0
        cst = int(cost) if cost.strip() else 0

        # Resolve parent_id (can be name or UUID)
        resolved_parent = resolve_task_reference(cur, project_id, parent_id)

        # Resolve dependencies (can use names or UUIDs)
        resolved_deps = resolve_dependencies(cur, project_id, dependencies_json)

        cur.execute(
            """INSERT INTO task (id, project_id, parent_id, type, name, start_date, end_date,
               assignee, percentage, status, cost, dependencies, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (
                task_id,
                project_id,
                resolved_parent,
                task_type,
                name.strip(),
                start_dt,
                end_dt,
                assignee.strip() if assignee.strip() else None,
                pct,
                status,
                cst,
                json.dumps(resolved_deps),
                now,
                now,
            ),
        )
        t = cur.fetchone()

        # Get name lookup for display
        name_lookup = get_task_name_lookup(cur, project_id)

        conn.commit()
        cur.close()
        conn.close()

        return f"✅ Task created successfully!\n\n{format_task(t, name_lookup)}"
    except json.JSONDecodeError as e:
        return f"❌ Error: Invalid JSON in dependencies_json: {str(e)}"
    except ValueError as e:
        return f"❌ Error: Invalid date format or number: {str(e)}"
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        return f"❌ Error: {str(e)}"


@mcp.tool()
async def bulk_create_tasks(project_id: str = "", tasks_json: str = "[]") -> str:
    """Create multiple tasks at once with proper parent nesting and dependencies. Use task NAMES for parent_id and dependency taskId - they will be resolved to UUIDs automatically."""
    logger.info(f"Bulk creating tasks in project {project_id}")

    if not project_id.strip():
        return "❌ Error: project_id is required"
    if not tasks_json.strip():
        return "❌ Error: tasks_json is required"
    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    try:
        tasks_data = json.loads(tasks_json)
        if not isinstance(tasks_data, list):
            return "❌ Error: tasks_json must be a JSON array"
        if not tasks_data:
            return "❌ Error: No tasks provided"

        conn = get_db_connection()
        cur = conn.cursor()

        # Verify project ownership
        cur.execute(
            "SELECT id FROM project WHERE id = %s AND user_id = %s",
            (project_id, USER_ID),
        )
        if not cur.fetchone():
            cur.close()
            conn.close()
            return f"❌ Project not found or not authorized: {project_id}"

        now = datetime.now(timezone.utc)
        valid_types = ["task", "group", "milestone"]
        valid_statuses = ["pending", "in-progress", "completed", "blocked"]

        # First pass: Create all tasks without parent_id and dependencies, build name-to-id mapping
        name_to_id = {}
        created_tasks = []
        task_parent_names = {}
        task_dependencies = {}

        for t in tasks_data:
            task_id = str(uuid4())
            name = t.get("name", "Untitled Task")
            task_type = t.get("task_type", t.get("type", "task"))
            status = t.get("status", "pending")

            if task_type not in valid_types:
                task_type = "task"
            if status not in valid_statuses:
                status = "pending"

            start_dt = None
            end_dt = None
            if t.get("start_date"):
                start_dt = datetime.fromisoformat(
                    str(t["start_date"]).replace("Z", "+00:00")
                )
            if t.get("end_date"):
                end_dt = datetime.fromisoformat(
                    str(t["end_date"]).replace("Z", "+00:00")
                )

            pct = int(t.get("percentage", 0))
            cst = int(t.get("cost", 0))
            assignee = t.get("assignee") or None

            # Store parent and dependency references for second pass
            parent_ref = t.get("parent_id", "")
            if parent_ref and parent_ref.strip():
                task_parent_names[task_id] = parent_ref.strip()

            deps_ref = t.get("dependencies", t.get("dependencies_json", []))
            if deps_ref:
                if isinstance(deps_ref, str):
                    try:
                        deps_ref = json.loads(deps_ref)
                    except:
                        deps_ref = []
                if deps_ref:
                    task_dependencies[task_id] = deps_ref

            # Insert without parent_id and dependencies first
            cur.execute(
                """INSERT INTO task (id, project_id, parent_id, type, name, start_date, end_date,
                   assignee, percentage, status, cost, dependencies, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, name""",
                (
                    task_id,
                    project_id,
                    None,
                    task_type,
                    name,
                    start_dt,
                    end_dt,
                    assignee,
                    pct,
                    status,
                    cst,
                    json.dumps([]),
                    now,
                    now,
                ),
            )
            created = cur.fetchone()
            created_tasks.append(created)
            name_to_id[name] = task_id

        # Second pass: Update parent_id references
        parent_count = 0
        for task_id, parent_name in task_parent_names.items():
            # Check if parent_name is already a UUID
            if len(parent_name) == 36 and parent_name.count("-") == 4:
                parent_id = parent_name
            else:
                parent_id = name_to_id.get(parent_name)

            if parent_id:
                cur.execute(
                    "UPDATE task SET parent_id = %s WHERE id = %s", (parent_id, task_id)
                )
                parent_count += 1

        # Third pass: Update dependencies
        dep_count = 0
        for task_id, deps in task_dependencies.items():
            resolved_deps = []
            for dep in deps:
                if isinstance(dep, dict):
                    task_ref = dep.get("taskId", dep.get("task_id", ""))
                    dep_type = dep.get("type", "FS")
                else:
                    continue

                if dep_type not in ["FS", "FF", "SS", "SF"]:
                    dep_type = "FS"

                # Resolve task reference
                if len(task_ref) == 36 and task_ref.count("-") == 4:
                    resolved_id = task_ref
                else:
                    resolved_id = name_to_id.get(task_ref)

                if resolved_id:
                    resolved_deps.append({"taskId": resolved_id, "type": dep_type})

            if resolved_deps:
                cur.execute(
                    "UPDATE task SET dependencies = %s WHERE id = %s",
                    (json.dumps(resolved_deps), task_id),
                )
                dep_count += 1

        conn.commit()
        cur.close()
        conn.close()

        result = f"✅ Successfully created {len(created_tasks)} task(s):\n"
        result += f"   - {parent_count} parent relationships resolved\n"
        result += f"   - {dep_count} tasks with dependencies resolved\n\n"
        for t in created_tasks:
            result += f"- **{t['name']}** (ID: `{t['id']}`)\n"

        return result
    except json.JSONDecodeError as e:
        return f"❌ Error: Invalid JSON: {str(e)}"
    except ValueError as e:
        return f"❌ Error: Invalid date format or number: {str(e)}"
    except Exception as e:
        logger.error(f"Error bulk creating tasks: {e}")
        return f"❌ Error: {str(e)}"


@mcp.tool()
async def update_task(
    task_id: str = "",
    project_id: str = "",
    name: str = "",
    task_type: str = "",
    status: str = "",
    start_date: str = "",
    end_date: str = "",
    assignee: str = "",
    percentage: str = "",
    cost: str = "",
    parent_id: str = "",
    dependencies_json: str = "",
) -> str:
    """Update an existing task. task_id and parent_id can be name or UUID. dependencies_json uses names or UUIDs. Only non-empty fields are updated."""
    logger.info(f"Updating task {task_id}")

    if not task_id.strip():
        return "❌ Error: task_id is required"
    if not project_id.strip():
        return "❌ Error: project_id is required"
    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    valid_types = ["task", "group", "milestone"]
    valid_statuses = ["pending", "in-progress", "completed", "blocked"]

    if task_type.strip() and task_type not in valid_types:
        return f"❌ Error: task_type must be one of: {', '.join(valid_types)}"
    if status.strip() and status not in valid_statuses:
        return f"❌ Error: status must be one of: {', '.join(valid_statuses)}"

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Verify project ownership
        cur.execute(
            "SELECT id FROM project WHERE id = %s AND user_id = %s",
            (project_id, USER_ID),
        )
        if not cur.fetchone():
            cur.close()
            conn.close()
            return f"❌ Project not found or not authorized: {project_id}"

        # Resolve task_id (can be name or UUID)
        resolved_task_id = resolve_task_reference(cur, project_id, task_id)
        if not resolved_task_id:
            cur.close()
            conn.close()
            return f"❌ Task not found: {task_id}"

        # Build update query dynamically
        updates = []
        values = []

        if name.strip():
            updates.append("name = %s")
            values.append(name.strip())
        if task_type.strip():
            updates.append("type = %s")
            values.append(task_type)
        if status.strip():
            updates.append("status = %s")
            values.append(status)
        if start_date.strip():
            updates.append("start_date = %s")
            values.append(datetime.fromisoformat(start_date.replace("Z", "+00:00")))
        if end_date.strip():
            updates.append("end_date = %s")
            values.append(datetime.fromisoformat(end_date.replace("Z", "+00:00")))
        if assignee.strip():
            updates.append("assignee = %s")
            values.append(assignee.strip())
        if percentage.strip():
            updates.append("percentage = %s")
            values.append(int(percentage))
        if cost.strip():
            updates.append("cost = %s")
            values.append(int(cost))
        if parent_id.strip():
            if parent_id.strip().lower() == "null" or parent_id.strip() == "":
                updates.append("parent_id = %s")
                values.append(None)
            else:
                resolved_parent = resolve_task_reference(cur, project_id, parent_id)
                updates.append("parent_id = %s")
                values.append(resolved_parent)
        if dependencies_json.strip():
            resolved_deps = resolve_dependencies(cur, project_id, dependencies_json)
            updates.append("dependencies = %s")
            values.append(json.dumps(resolved_deps))

        if not updates:
            cur.close()
            conn.close()
            return "❌ Error: No fields to update provided"

        updates.append("updated_at = %s")
        values.append(datetime.now(timezone.utc))
        values.extend([resolved_task_id, project_id])

        query = f"UPDATE task SET {', '.join(updates)} WHERE id = %s AND project_id = %s RETURNING *"
        cur.execute(query, values)
        t = cur.fetchone()

        name_lookup = get_task_name_lookup(cur, project_id)

        conn.commit()
        cur.close()
        conn.close()

        if not t:
            return f"❌ Task not found: {task_id}"

        return f"✅ Task updated successfully!\n\n{format_task(t, name_lookup)}"
    except json.JSONDecodeError as e:
        return f"❌ Error: Invalid JSON in dependencies_json: {str(e)}"
    except ValueError as e:
        return f"❌ Error: Invalid date format or number: {str(e)}"
    except Exception as e:
        logger.error(f"Error updating task: {e}")
        return f"❌ Error: {str(e)}"


@mcp.tool()
async def delete_task(
    task_id: str = "", project_id: str = "", delete_children: str = "false"
) -> str:
    """Delete a task by ID or name. Set delete_children to 'true' to also delete all nested child tasks."""
    logger.info(f"Deleting task {task_id}")

    if not task_id.strip():
        return "❌ Error: task_id is required"
    if not project_id.strip():
        return "❌ Error: project_id is required"
    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Verify project ownership
        cur.execute(
            "SELECT id FROM project WHERE id = %s AND user_id = %s",
            (project_id, USER_ID),
        )
        if not cur.fetchone():
            cur.close()
            conn.close()
            return f"❌ Project not found or not authorized: {project_id}"

        # Resolve task_id
        resolved_task_id = resolve_task_reference(cur, project_id, task_id)
        if not resolved_task_id:
            cur.close()
            conn.close()
            return f"❌ Task not found: {task_id}"

        deleted_tasks = []

        if delete_children.lower() == "true":
            # Recursively find all children
            def get_all_children(parent_id):
                cur.execute(
                    "SELECT id, name FROM task WHERE parent_id = %s", (parent_id,)
                )
                children = cur.fetchall()
                all_ids = []
                for child in children:
                    all_ids.append(child)
                    all_ids.extend(get_all_children(child["id"]))
                return all_ids

            children = get_all_children(resolved_task_id)

            # Delete children first (bottom-up)
            for child in reversed(children):
                cur.execute(
                    "DELETE FROM task WHERE id = %s RETURNING id, name", (child["id"],)
                )
                deleted = cur.fetchone()
                if deleted:
                    deleted_tasks.append(deleted)

        # Delete the main task
        cur.execute(
            "DELETE FROM task WHERE id = %s AND project_id = %s RETURNING id, name",
            (resolved_task_id, project_id),
        )
        deleted = cur.fetchone()

        # Update any tasks that had this as parent
        cur.execute(
            "UPDATE task SET parent_id = NULL WHERE parent_id = %s", (resolved_task_id,)
        )
        orphaned = cur.rowcount

        # Remove from dependencies
        cur.execute(
            "SELECT id, dependencies FROM task WHERE project_id = %s AND dependencies IS NOT NULL",
            (project_id,),
        )
        tasks_with_deps = cur.fetchall()
        for t in tasks_with_deps:
            deps = t["dependencies"] or []
            new_deps = [d for d in deps if d.get("taskId") != resolved_task_id]
            if len(new_deps) != len(deps):
                cur.execute(
                    "UPDATE task SET dependencies = %s WHERE id = %s",
                    (json.dumps(new_deps), t["id"]),
                )

        conn.commit()
        cur.close()
        conn.close()

        if not deleted:
            return f"❌ Task not found: {task_id}"

        result = f"✅ Task '{deleted['name']}' deleted successfully."
        if deleted_tasks:
            result += f"\n   Also deleted {len(deleted_tasks)} child task(s)."
        if orphaned > 0:
            result += f"\n   {orphaned} task(s) were orphaned (parent removed)."

        return result
    except Exception as e:
        logger.error(f"Error deleting task: {e}")
        return f"❌ Error: {str(e)}"


@mcp.tool()
async def batch_update_tasks(project_id: str = "", updates_json: str = "[]") -> str:
    """Batch update multiple tasks. updates_json: array of objects with 'id' (name or UUID) and fields to update. parent_id and dependencies can use names."""
    logger.info(f"Batch updating tasks in project {project_id}")

    if not project_id.strip():
        return "❌ Error: project_id is required"
    if not updates_json.strip():
        return "❌ Error: updates_json is required"
    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    try:
        updates = json.loads(updates_json)
        if not isinstance(updates, list):
            return "❌ Error: updates_json must be a JSON array"
        if not updates:
            return "❌ Error: No updates provided"

        conn = get_db_connection()
        cur = conn.cursor()

        # Verify project ownership
        cur.execute(
            "SELECT id FROM project WHERE id = %s AND user_id = %s",
            (project_id, USER_ID),
        )
        if not cur.fetchone():
            cur.close()
            conn.close()
            return f"❌ Project not found or not authorized: {project_id}"

        # Build name lookup
        name_lookup = get_task_name_lookup(cur, project_id)
        id_lookup = {v: k for k, v in name_lookup.items()}  # Reverse lookup

        updated_count = 0
        now = datetime.now(timezone.utc)
        errors = []

        for update in updates:
            if not isinstance(update, dict) or "id" not in update:
                continue

            task_ref = update["id"]
            resolved_task_id = resolve_task_reference(cur, project_id, task_ref)

            if not resolved_task_id:
                errors.append(f"Task not found: {task_ref}")
                continue

            fields = []
            values = []

            for key, value in update.items():
                if key == "id":
                    continue
                if key in ["projectId", "project_id"]:
                    continue
                if key in ["startDate", "start_date"]:
                    fields.append("start_date = %s")
                    values.append(
                        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                        if value
                        else None
                    )
                elif key in ["endDate", "end_date"]:
                    fields.append("end_date = %s")
                    values.append(
                        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                        if value
                        else None
                    )
                elif key in ["parentId", "parent_id"]:
                    if value and str(value).strip().lower() != "null":
                        resolved_parent = resolve_task_reference(
                            cur, project_id, str(value)
                        )
                        fields.append("parent_id = %s")
                        values.append(resolved_parent)
                    else:
                        fields.append("parent_id = %s")
                        values.append(None)
                elif key == "dependencies":
                    if isinstance(value, str):
                        value = json.loads(value)
                    resolved_deps = []
                    for dep in value or []:
                        task_dep_ref = dep.get("taskId", dep.get("task_id", ""))
                        dep_type = dep.get("type", "FS")
                        resolved_dep_id = resolve_task_reference(
                            cur, project_id, task_dep_ref
                        )
                        if resolved_dep_id:
                            resolved_deps.append(
                                {"taskId": resolved_dep_id, "type": dep_type}
                            )
                    fields.append("dependencies = %s")
                    values.append(json.dumps(resolved_deps))
                elif key in ["name", "type", "status", "assignee"]:
                    fields.append(f"{key} = %s")
                    values.append(value)
                elif key in ["percentage", "cost", "duration"]:
                    fields.append(f"{key} = %s")
                    values.append(int(value) if value else 0)

            if fields:
                fields.append("updated_at = %s")
                values.append(now)
                values.append(resolved_task_id)

                query = f"UPDATE task SET {', '.join(fields)} WHERE id = %s"
                cur.execute(query, values)
                updated_count += cur.rowcount

        conn.commit()
        cur.close()
        conn.close()

        result = f"✅ Batch update complete. Updated {updated_count} task(s)."
        if errors:
            result += f"\n\n⚠️ Warnings:\n" + "\n".join(f"- {e}" for e in errors)

        return result
    except json.JSONDecodeError as e:
        return f"❌ Error: Invalid JSON: {str(e)}"
    except Exception as e:
        logger.error(f"Error batch updating tasks: {e}")
        return f"❌ Error: {str(e)}"


@mcp.tool()
async def move_task(
    task_id: str = "", project_id: str = "", new_parent_id: str = ""
) -> str:
    """Move a task to a new parent. Use task names or UUIDs. Set new_parent_id to 'null' or empty to make it a root task."""
    logger.info(f"Moving task {task_id} to parent {new_parent_id}")

    if not task_id.strip():
        return "❌ Error: task_id is required"
    if not project_id.strip():
        return "❌ Error: project_id is required"
    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Verify project ownership
        cur.execute(
            "SELECT id FROM project WHERE id = %s AND user_id = %s",
            (project_id, USER_ID),
        )
        if not cur.fetchone():
            cur.close()
            conn.close()
            return f"❌ Project not found or not authorized: {project_id}"

        # Resolve task_id
        resolved_task_id = resolve_task_reference(cur, project_id, task_id)
        if not resolved_task_id:
            cur.close()
            conn.close()
            return f"❌ Task not found: {task_id}"

        # Resolve new parent
        resolved_parent = None
        if new_parent_id.strip() and new_parent_id.strip().lower() != "null":
            resolved_parent = resolve_task_reference(cur, project_id, new_parent_id)
            if not resolved_parent:
                cur.close()
                conn.close()
                return f"❌ Parent task not found: {new_parent_id}"

            # Check for circular reference
            if resolved_parent == resolved_task_id:
                cur.close()
                conn.close()
                return "❌ Error: Cannot set a task as its own parent"

            # Check if new parent is a descendant of the task
            def is_descendant(parent_id, target_id):
                cur.execute("SELECT id FROM task WHERE parent_id = %s", (parent_id,))
                children = cur.fetchall()
                for child in children:
                    if child["id"] == target_id:
                        return True
                    if is_descendant(child["id"], target_id):
                        return True
                return False

            if is_descendant(resolved_task_id, resolved_parent):
                cur.close()
                conn.close()
                return "❌ Error: Cannot move a task under its own descendant (circular reference)"

        # Update the task
        cur.execute(
            "UPDATE task SET parent_id = %s, updated_at = %s WHERE id = %s RETURNING *",
            (resolved_parent, datetime.now(timezone.utc), resolved_task_id),
        )
        t = cur.fetchone()

        name_lookup = get_task_name_lookup(cur, project_id)

        conn.commit()
        cur.close()
        conn.close()

        parent_name = (
            name_lookup.get(resolved_parent, "Root") if resolved_parent else "Root"
        )
        return f"✅ Task moved successfully!\n\nNew parent: **{parent_name}**\n\n{format_task(t, name_lookup)}"
    except Exception as e:
        logger.error(f"Error moving task: {e}")
        return f"❌ Error: {str(e)}"


@mcp.tool()
async def add_dependency(
    task_id: str = "",
    project_id: str = "",
    depends_on: str = "",
    dependency_type: str = "FS",
) -> str:
    """Add a dependency to a task. task_id and depends_on can be names or UUIDs. dependency_type: FS (Finish-Start), FF, SS, SF."""
    logger.info(f"Adding dependency: {task_id} depends on {depends_on}")

    if not task_id.strip():
        return "❌ Error: task_id is required"
    if not project_id.strip():
        return "❌ Error: project_id is required"
    if not depends_on.strip():
        return "❌ Error: depends_on is required"
    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    valid_dep_types = ["FS", "FF", "SS", "SF"]
    if dependency_type not in valid_dep_types:
        return f"❌ Error: dependency_type must be one of: {', '.join(valid_dep_types)}"

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Verify project ownership
        cur.execute(
            "SELECT id FROM project WHERE id = %s AND user_id = %s",
            (project_id, USER_ID),
        )
        if not cur.fetchone():
            cur.close()
            conn.close()
            return f"❌ Project not found or not authorized: {project_id}"

        # Resolve both tasks
        resolved_task_id = resolve_task_reference(cur, project_id, task_id)
        if not resolved_task_id:
            cur.close()
            conn.close()
            return f"❌ Task not found: {task_id}"

        resolved_depends_on = resolve_task_reference(cur, project_id, depends_on)
        if not resolved_depends_on:
            cur.close()
            conn.close()
            return f"❌ Dependency task not found: {depends_on}"

        if resolved_task_id == resolved_depends_on:
            cur.close()
            conn.close()
            return "❌ Error: A task cannot depend on itself"

        # Get current dependencies
        cur.execute("SELECT dependencies FROM task WHERE id = %s", (resolved_task_id,))
        result = cur.fetchone()
        current_deps = result["dependencies"] or []

        # Check if dependency already exists
        for dep in current_deps:
            if dep.get("taskId") == resolved_depends_on:
                cur.close()
                conn.close()
                name_lookup = get_task_name_lookup(cur, project_id)
                return f"⚠️ Dependency already exists: {name_lookup.get(resolved_depends_on, resolved_depends_on)}"

        # Add new dependency
        current_deps.append({"taskId": resolved_depends_on, "type": dependency_type})

        cur.execute(
            "UPDATE task SET dependencies = %s, updated_at = %s WHERE id = %s RETURNING *",
            (json.dumps(current_deps), datetime.now(timezone.utc), resolved_task_id),
        )
        t = cur.fetchone()

        name_lookup = get_task_name_lookup(cur, project_id)

        conn.commit()
        cur.close()
        conn.close()

        dep_task_name = name_lookup.get(resolved_depends_on, resolved_depends_on)
        return f"✅ Dependency added: **{t['name']}** now depends on **{dep_task_name}** ({dependency_type})\n\n{format_task(t, name_lookup)}"
    except Exception as e:
        logger.error(f"Error adding dependency: {e}")
        return f"❌ Error: {str(e)}"


@mcp.tool()
async def remove_dependency(
    task_id: str = "", project_id: str = "", depends_on: str = ""
) -> str:
    """Remove a dependency from a task. task_id and depends_on can be names or UUIDs."""
    logger.info(f"Removing dependency: {task_id} no longer depends on {depends_on}")

    if not task_id.strip():
        return "❌ Error: task_id is required"
    if not project_id.strip():
        return "❌ Error: project_id is required"
    if not depends_on.strip():
        return "❌ Error: depends_on is required"
    if not USER_ID:
        return "❌ Error: SYNCPATH_USER_ID not configured"

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Verify project ownership
        cur.execute(
            "SELECT id FROM project WHERE id = %s AND user_id = %s",
            (project_id, USER_ID),
        )
        if not cur.fetchone():
            cur.close()
            conn.close()
            return f"❌ Project not found or not authorized: {project_id}"

        # Resolve both tasks
        resolved_task_id = resolve_task_reference(cur, project_id, task_id)
        if not resolved_task_id:
            cur.close()
            conn.close()
            return f"❌ Task not found: {task_id}"

        resolved_depends_on = resolve_task_reference(cur, project_id, depends_on)
        if not resolved_depends_on:
            cur.close()
            conn.close()
            return f"❌ Dependency task not found: {depends_on}"

        # Get current dependencies
        cur.execute("SELECT dependencies FROM task WHERE id = %s", (resolved_task_id,))
        result = cur.fetchone()
        current_deps = result["dependencies"] or []

        # Remove dependency
        new_deps = [d for d in current_deps if d.get("taskId") != resolved_depends_on]

        if len(new_deps) == len(current_deps):
            name_lookup = get_task_name_lookup(cur, project_id)
            cur.close()
            conn.close()
            return f"⚠️ Dependency not found: {name_lookup.get(resolved_depends_on, resolved_depends_on)}"

        cur.execute(
            "UPDATE task SET dependencies = %s, updated_at = %s WHERE id = %s RETURNING *",
            (json.dumps(new_deps), datetime.now(timezone.utc), resolved_task_id),
        )
        t = cur.fetchone()

        name_lookup = get_task_name_lookup(cur, project_id)

        conn.commit()
        cur.close()
        conn.close()

        dep_task_name = name_lookup.get(resolved_depends_on, resolved_depends_on)
        return f"✅ Dependency removed: **{t['name']}** no longer depends on **{dep_task_name}**\n\n{format_task(t, name_lookup)}"
    except Exception as e:
        logger.error(f"Error removing dependency: {e}")
        return f"❌ Error: {str(e)}"


# === SERVER STARTUP ===
if __name__ == "__main__":
    logger.info("Starting SyncPath MCP server...")

    if not DATABASE_URL:
        logger.warning("SYNCPATH_DATABASE_URL not set")
    if not USER_ID:
        logger.warning("SYNCPATH_USER_ID not set")

    try:
        mcp.run(transport="stdio")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        sys.exit(1)
