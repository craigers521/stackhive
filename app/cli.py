"""Flask CLI commands: user creation, credential seeding, inventory sync.

Commands are attached to the application CLI by ``create_app`` so that
``flask create-admin`` style invocations run inside an app context.
"""
import click

from .extensions import db
from .models import Role, User


@click.command("create-admin")
@click.option("--username", required=True, help="Login username.")
@click.option("--email", required=True, help="User email address.")
@click.option("--password", required=True, help="Initial plaintext password.")
def create_admin(username, email, password):
    """Create an admin user."""
    from flask import current_app

    _create_user(username, email, password, Role.ADMIN.value)
    current_app.logger.info("cli_create_admin username=%s", username)


@click.command("create-user")
@click.option("--username", required=True, help="Login username (3-128 chars).")
@click.option("--email", required=True, help="User email address.")
@click.option("--password", required=True, help="Initial plaintext password.")
@click.option(
    "--role",
    type=click.Choice([r.value for r in Role]),
    default=Role.VIEWER.value,
    show_default=True,
    help="Role to assign.",
)
def create_user(username, email, password, role):
    """Create a platform user with the given role."""
    from flask import current_app

    _create_user(username, email, password, role)
    current_app.logger.info("cli_create_user username=%s role=%s", username, role)


def _create_user(username, email, password, role):
    """Create the user row; idempotent with a clear error on duplicates."""
    if User.query.filter_by(username=username).first():
        raise click.ClickException(f"Username already exists: {username}")
    if User.query.filter_by(email=email).first():
        raise click.ClickException(f"Email already exists: {email}")
    user = User(username=username, email=email, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    click.echo(f"Created user {username} with role {role}")


@click.command("seed-credentials")
@click.option("--netbox-token", default=None, help="NetBox API token.")
@click.option("--gitlab-token", default=None, help="GitLab API token.")
@click.option("--grafana-token", default=None, help="Grafana service account token.")
@click.option("--meraki-token", default=None, help="Meraki Dashboard API key.")
def seed_credentials(netbox_token, gitlab_token, grafana_token, meraki_token):
    """Upsert Fernet-encrypted service credentials (initial bootstrap)."""
    from flask import current_app

    from .services import credential

    tokens = {
        "netbox": netbox_token,
        "gitlab": gitlab_token,
        "grafana": grafana_token,
        "meraki": meraki_token,
    }
    seeded = []
    for service_name, token in tokens.items():
        if token:
            credential.upsert_credential(current_app, service_name, token)
            seeded.append(service_name)
    if not seeded:
        raise click.ClickException("No tokens provided; pass at least one --*-token option")
    click.echo(f"Seeded credentials for: {', '.join(seeded)}")


@click.command("sync-inventory")
def sync_inventory_command():
    """Pull the device inventory from NetBox into the local cache."""
    from flask import current_app

    from .services.netbox import sync_inventory

    report = sync_inventory(current_app)
    click.echo("Sync report: " + str(report))


ALL_COMMANDS = [create_admin, create_user, seed_credentials, sync_inventory_command]
