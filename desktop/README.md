# MemoryWorks desktop bridge

This Tauri 2 client is intentionally thin. It owns OS integration—keychain storage, explicit folder selection, local/private-network probes, notifications, a local MCP sidecar, and signed updates. Connector manifests, OAuth grants, sync state, approvals, audit records, retrieval, and business logic remain in the cloud control plane.

## Development

```bash
npm install
npm run tauri:dev
```

Before packaging, place a target-specific `orgmemory-mcp` sidecar in `src-tauri/binaries/`, set the release updater public key/endpoint, and sign the updater artifacts. The sidecar is the existing `mcp_server/server.py` packaged as an executable; it receives a revocable workspace credential from the OS keychain only when launched.
