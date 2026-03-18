# SSH Profile Security

## What Is Stored

Each SSH profile record contains:

| Field | Stored | Example |
|-------|--------|---------|
| host | ✅ | `login.cluster.example.edu` |
| port | ✅ | `22` |
| username | ✅ | `jsmith` |
| auth_method | ✅ | `key_file` or `ssh_agent` |
| key_file_path | ✅ | `/home/agoutic/.ssh/id_ed25519` (path reference only) |
| nickname | ✅ | `localCluster` |
| local_username | ✅ | `jsmith` |
| remote_base_path | ✅ | `/scratch/jsmith/agoutic` |

## What Is NOT Stored

AGOUTIC **never** stores:

- Private key contents
- Passwords or passphrases
- SSH agent socket paths
- Any raw credential material

The `key_file_path` field stores only a filesystem path. The actual private key remains on disk, managed by the operating system.

## Authentication Methods

### `key_file`

- Stores the **path** to a private key file on the server's filesystem.
- In brokered shared-user deployments, AGOUTIC can launch a local auth broker under `local_username` so the SSH client reads the key as that Unix user instead of the service account.
- Without the broker path, the key file must be readable by the AGOUTIC server process.
- If the key is passphrase-protected, the passphrase must be loaded into an SSH agent beforehand.

### `ssh_agent`

- Delegates authentication entirely to the system's SSH agent (`ssh-agent` or compatible).
- No key path is stored.
- The agent must be running and the appropriate key must be loaded (`ssh-add`).
- **Recommended for production** — keys never touch the application layer.

## Per-User Isolation

All SSH profile queries are scoped by `user_id`:

```sql
SELECT * FROM ssh_profiles WHERE user_id = :current_user AND id = :profile_id;
```

- A user can only view, edit, and delete their own profiles.
- API endpoints enforce this at the query level — there is no admin bypass for profile access.
- Profile IDs are UUIDs; enumeration is not feasible.

## Credential Logging

Audit logs (`RunAuditLog`) record:

- `ssh_profile_id` — which profile was used
- `timestamp` — when the connection was made
- `event` — what action was performed (connect, transfer, submit, etc.)

Audit logs **never** contain:

- Key file contents
- Passwords or passphrases
- Connection strings with embedded credentials

## Key File Access

When using `key_file` auth, the server process must have read access to the key file at runtime:

```bash
# Key file must be owned by the service user and restricted
chmod 600 /home/agoutic/.ssh/id_ed25519
chown agoutic:agoutic /home/agoutic/.ssh/id_ed25519
```

If the server cannot read the key file, the connection will fail with a clear error (not a silent fallback).

## Brokered Local-User Access

When a profile stores both `key_file_path` and `local_username`, AGOUTIC can use
the local auth broker flow instead of reading the key directly as the service user.

- The user unlocks the profile with their local Unix password
- AGOUTIC launches a broker under `su <local_username>`
- The password is used transiently and is never stored
- Session metadata is written under `AGOUTIC_DATA/runtime/local_auth`
- Later SSH and rsync operations reuse the unlocked session across Launchpad processes

This is the preferred model for shared-user deployments where the service
account should not own every user's private key.

## Recommendations

| Practice | Details |
|----------|---------|
| **Use brokered `key_file + local_username` or `ssh_agent`** | Prefer models that avoid widening direct key readability to the service account |
| **Rotate keys periodically** | Generate new key pairs and update authorized_keys on the cluster |
| **Restrict key file permissions** | `chmod 600` on private keys; `chmod 644` on public keys |
| **Use Ed25519 keys** | Preferred over RSA for security and performance |
| **Limit key scope** | Use `command=` or `from=` restrictions in `authorized_keys` where possible |
| **Monitor audit logs** | Review `RunAuditLog` for unexpected connection patterns |
| **Separate keys per environment** | Use different key pairs for dev, staging, and production |
