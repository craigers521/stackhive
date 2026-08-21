"""Dashboard blueprint: post-login landing page and summary API."""
from flask import Blueprint, jsonify, redirect, render_template, url_for

from ..decorators import viewer_required
from ..models import Device, DeploymentRecord, ZTPProvision

bp = Blueprint("dashboard", __name__)


def _summary_payload():
    """Build the shared dashboard summary (health, deployments, approvals)."""
    total = Device.query.count()
    up = Device.query.filter_by(monitoring_status="up").count()
    down = Device.query.filter_by(monitoring_status="down").count()
    recent = (
        DeploymentRecord.query.order_by(DeploymentRecord.started_at.desc()).limit(5).all()
    )
    pending = DeploymentRecord.query.filter_by(status="pending").order_by(
        DeploymentRecord.started_at.desc()
    ).all()
    active_ztp = ZTPProvision.query.filter(
        ZTPProvision.status.in_(("pending", "generated", "delivered"))
    ).count()
    return {
        "device_health": {"up": up, "down": down, "total": total},
        "recent_deployments": [d.to_dict() for d in recent],
        "pending_approvals": [d.to_dict() for d in pending],
        "ztp": {"active": active_ztp},
    }


@bp.get("/")
def index():
    """Landing page after login; unauthenticated users go to the login form."""
    from flask_login import current_user

    if not current_user.is_authenticated:
        return redirect(url_for("auth.login_page"))
    payload = _summary_payload()
    return render_template("dashboard/index.html", summary=payload)


@bp.get("/api/dashboard")
@viewer_required
def api_dashboard():
    """JSON summary used by the landing page and HTMX polling."""
    return jsonify(_summary_payload())
