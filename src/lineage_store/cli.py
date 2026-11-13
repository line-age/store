"""Admin commands, run where the database is reachable.

    lineage-store serve
    lineage-store token create backport --pipeline backport@742973e --assert-for-others
    lineage-store token revoke <token-id>
    lineage-store role map jdoe --github jdoe        # a QGIS database role -> that person's agent
    lineage-store qgis install --host db.example --port 5432   # the ready-made QGIS project
"""

from __future__ import annotations

import argparse
import sys

from .auth import new_token
from .config import Settings
from .db import STORE_AGENT, Database


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="lineage-store", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the API")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)

    tok = sub.add_parser("token", help="pipeline tokens").add_subparsers(dest="tcmd", required=True)
    c = tok.add_parser("create", help="make a pipeline agent (if new) and a token for it")
    c.add_argument("name")
    c.add_argument("--pipeline", help="name and version, e.g. backport@742973e")
    c.add_argument("--assert-for-others", action="store_true",
                   help="may write claims asserted by other agents (importers)")
    r = tok.add_parser("revoke")
    r.add_argument("token_id")

    role = sub.add_parser("role", help="database roles for direct QGIS access").add_subparsers(dest="rcmd", required=True)
    m = role.add_parser("map", help="attribute a database role's writes to a person")
    m.add_argument("role")
    m.add_argument("--github", required=True, help="the person's GitHub login")
    m.add_argument("--name", help="their name, if they have no agent yet")
    m.add_argument("--email", help="their email, if they have no agent yet")

    q = sub.add_parser("qgis", help="QGIS project and style in the database").add_subparsers(dest="qcmd", required=True)
    qi = q.add_parser("install", help="install or replace the Lineage project and the default sections style")
    qi.add_argument("--host", required=True, help="the database host as QGIS users reach it")
    qi.add_argument("--port", type=int, default=5432)
    qi.add_argument("--dbname", default="lineage")
    qi.add_argument("--user", help="put a user in the project (local development only)")
    qi.add_argument("--password", help="put a password in the project (local development only)")

    args = ap.parse_args(argv)
    if args.cmd == "serve":
        import uvicorn

        from .app import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)
        return

    db = Database(Settings().database_url)
    try:
        with db.write(STORE_AGENT) as conn:
            if args.cmd == "token" and args.tcmd == "create":
                row = conn.execute("SELECT id, kind FROM store.agent WHERE kind = 'pipeline' AND name = %s",
                                   (args.name,)).fetchone()
                if row:
                    agent_id = row["id"]
                    if args.pipeline:
                        conn.execute("UPDATE store.agent SET pipeline = %s WHERE id = %s", (args.pipeline, agent_id))
                else:
                    agent_id = conn.execute(
                        "INSERT INTO store.agent (kind, name, pipeline) VALUES ('pipeline', %s, %s) RETURNING id",
                        (args.name, args.pipeline or args.name)).fetchone()["id"]
                token, digest = new_token()
                tid = conn.execute(
                    "INSERT INTO store.agent_token (agent_id, token_sha256, may_assert_for_others) "
                    "VALUES (%s, %s, %s) RETURNING id", (agent_id, digest, args.assert_for_others)).fetchone()["id"]
                print(f"agent {agent_id}\ntoken id {tid}\n{token}")
                print("The token is shown once; only its hash is stored.", file=sys.stderr)
            elif args.cmd == "token" and args.tcmd == "revoke":
                n = conn.execute("UPDATE store.agent_token SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL",
                                 (args.token_id,)).rowcount
                print("revoked" if n else "no live token with that id")
            elif args.cmd == "qgis":
                from . import qgis
                cs = qgis.connection_string(args.host, args.port, args.dbname, args.user, args.password)
                qgis.install(conn, cs, args.dbname)
                print(f"installed QGIS project {qgis.PROJECT_NAME!r} in qgis.qgis_projects for {args.host}:{args.port}")
            elif args.cmd == "role":
                row = conn.execute("SELECT id FROM store.agent WHERE github_login = %s", (args.github,)).fetchone()
                agent_id = row["id"] if row else conn.execute(
                    "INSERT INTO store.agent (kind, name, github_login, email) VALUES ('person', %s, %s, %s) RETURNING id",
                    (args.name or args.github, args.github, args.email)).fetchone()["id"]
                conn.execute("INSERT INTO store.agent_role (role, agent_id) VALUES (%s, %s) "
                             "ON CONFLICT (role) DO UPDATE SET agent_id = EXCLUDED.agent_id", (args.role, agent_id))
                print(f"role {args.role} -> agent {agent_id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
