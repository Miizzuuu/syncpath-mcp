# SyncPath MCP Server

## Tujuan

MCP Server ini memberikan akses operasi CRUD ke AI supaya bisa manage projects dan tasks di aplikasi Syncpath. MCP akan connect langsung ke database NeonDB PostgreSQL milik anda.

**Disclaimer:** <small>Tidak harus menggunakan Claude Desktop dan NeonDB PostgresSQL, anda dapat menggunakan Client dan Service lain. Claude Desktop dan NeonDB PostgreSQL hanya sebagai client dan service yang digunakan oleh tim Syncpath sebagai contoh.</small>

## Tools

### Project Management
- **`list_projects`** - list semua project milik user yang login
- **`get_project`** - ambil detail project tertentu pake ID
- **`create_project`** - bikin project baru dengan nama tertentu
- **`update_project`** - update nama project
- **`delete_project`** - hapus project beserta semua tasknya

### Task Management
- **`list_tasks`** - list semua task dari project tertentu
- **`get_task`** - ambil detail task tertentu pake ID
- **`create_task`** - bikin task baru dengan konfigurasi lengkap (type, status, tanggal, assignee, dll.)
- **`update_task`** - update field task apapun
- **`delete_task`** - hapus task
- **`batch_update_tasks`** - update banyak task sekaligus

## Requirements

- Docker Desktop dengan MCP Toolkit yang udah di-enable
- Plugin Docker MCP CLI (command `docker mcp`)
- URL database NeonDB PostgreSQL
- User ID SyncPath anda

## Contoh Penggunaan

Di Claude Desktop, anda bisa tanya:

- "List semua project SyncPath gue"
- "Bikin project baru namanya 'Website Redesign'"
- "Tunjukin semua task di project [project-id]"
- "Bikin task namanya 'Design mockups' di project [id] mulai 2025-01-15 sampe 2025-01-20"
- "Update task [id] jadi status 'completed' dengan progress 100%"
- "Hapus task [id] dari project [project-id]"
- "Batch update tasks buat set progress jadi 50%"

## Arsitektur

```
Claude Desktop (Client) → MCP Server → NeonDB PostgreSQL (Service)
```

## Troubleshooting

### Tools Tidak Muncul
- Pastiin Docker image udah ke-build dengan sukses
- Cek file catalog sama registry
- Pastiin config Claude Desktop udah include custom catalog
- Restart Claude Desktop

### Error Koneksi Database
- Cek connection string NeonDB lo bener gak
- Pastiin database bisa diakses dari Docker

### Error Authorization
- Pastiin SYNCPATH_USER_ID anda benar
- Pastiin user ada di database
