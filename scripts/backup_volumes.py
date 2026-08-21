#!/usr/bin/env python3
"""Daily Docker volume backup with 7-daily + 4-weekly retention.

Implements the plan.md Non-Functional Decision "Backup & recovery": a daily
job archives every named StackHive compose volume into ``backups/`` and prunes
older archives (7 daily + 4 weekly kept per volume). Recovery is recreate an
empty volume, untar the latest archive into it, ``docker compose up -d``
(see README.md "Backups & recovery").

Each volume is archived by a throwaway busybox container so the host needs
no special tooling beyond the Docker CLI:

    docker run --rm -v <volume>:/data:ro -v <backup-dir>:/backup busybox \
        tar czf /backup/<archive> -C /data .

Usage:
    python3 scripts/backup_volumes.py [--backup-dir DIR] [--volumes a,b]
        [--image busybox:1.36] [--weekly] [--dry-run] [--verbose]

Cron (daily at 02:00 local):
    0 2 * * * /usr/bin/python3 /opt/stackhive/scripts/backup_volumes.py \
        --backup-dir /opt/stackhive/backups >> /var/log/stackhive-backup.log 2>&1
"""
import argparse
import datetime
import glob
import logging
import os
import re
import shutil
import subprocess
import sys

logger = logging.getLogger("backup-volumes")

DEFAULT_VOLUMES = (
    "stackhive-db",
    "gitlab-data",
    "gitlab-etc",
    "gitlab-logs",
    "influxdb-data",
    "grafana-data",
    "traefik-acme",
    "runner-config",
)
DEFAULT_IMAGE = "busybox:1.36"
DEFAULT_BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups")
DAILY_KEEP = 7
WEEKLY_KEEP = 4
STAMP_RE = re.compile(r"stackhive-(?P<volume>.+)-(?P<stamp>(?:\d{4}-\d{2}-\d{2}|\d{4}-W\d{2})).tar\.gz$")


def run_command(cmd, dry_run=False):
    """Run a command; in dry-run mode log it instead of executing."""
    if dry_run:
        logger.info("dry-run: %s", " ".join(str(c) for c in cmd))
        return 0, ""
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    return proc.returncode, (proc.stderr or proc.stdout or "").strip()


def archive_stamps(now=None):
    """Return (daily_stamp, weekly_stamp) for a date (default today)."""
    day = now or datetime.date.today()
    iso_year, iso_week, _ = day.isocalendar()
    return day.strftime("%Y-%m-%d"), f"{iso_year}-W{iso_week:02d}"


def archive_name(volume, stamp):
    """Archive file name for a volume and (daily or weekly) stamp."""
    return f"stackhive-{volume}-{stamp}.tar.gz"


def is_weekly_stamp(stamp):
    """True when the stamp marks a weekly archive (ISO year-week)."""
    return "-W" in stamp


def select_to_delete(names, keep):
    """Names from a sorted-by-name list that exceed the keep count.

    Archive names are lexicographically sortable by age, so the oldest
    archives sort first. Returns the names to delete (all but the newest
    ``keep`` entries); an empty list when nothing exceeds retention.
    """
    ordered = sorted(names)
    if len(ordered) <= keep:
        return []
    return ordered[: len(ordered) - keep]


def tar_volume(volume, backup_dir, image, docker="docker", dry_run=False):
    """Archive one named volume into backup_dir; returns (ok, detail)."""
    code, out = run_command([docker, "volume", "inspect", volume], dry_run=dry_run)
    if code != 0:
        logger.warning("volume_missing volume=%s (docker will auto-create on archive; skipping)", volume)
        return False, "volume not found"
    os.makedirs(backup_dir, exist_ok=True)
    stamp = archive_stamps()[0]
    archive = archive_name(volume, stamp)
    dest = os.path.join(backup_dir, archive)
    code, out = run_command(
        [docker, "run", "--rm", "-v", f"{volume}:/data:ro", "-v", f"{backup_dir}:/backup",
         image, "tar", "czf", "/backup/" + archive, "-C", "/data", "."],
        dry_run=dry_run,
    )
    if code != 0:
        logger.error("archive_failed volume=%s detail=%s", volume, out)
        return False, out
    logger.info("archived volume=%s archive=%s", volume, archive)
    return True, archive


def ensure_image(image, docker="docker", dry_run=False):
    """Pull the archive helper image on first use (minimal OSS image)."""
    code, _ = run_command([docker, "image", "inspect", image], dry_run=dry_run)
    if code != 0:
        logger.info("pulling image=%s", image)
        code, out = run_command([docker, "pull", image], dry_run=dry_run)
        if code != 0:
            raise RuntimeExit(f"could not pull {image}: {out}")


def prune_volume(volume, backup_dir, dry_run=False):
    """Delete per-volume archives beyond the 7-daily + 4-weekly retention."""
    daily, weekly = [], []
    for path in glob.glob(os.path.join(backup_dir, f"stackhive-{volume}-*.tar.gz")):
        name = os.path.basename(path)
        match = STAMP_RE.match(name)
        if not match or match.group("volume") != volume:
            continue
        (weekly if is_weekly_stamp(match.group("stamp")) else daily).append(path)
    doomed = set(select_to_delete(daily, DAILY_KEEP)) | set(select_to_delete(weekly, WEEKLY_KEEP))
    for path in sorted(doomed):
        if dry_run:
            logger.info("dry-run: prune %s", path)
        else:
            os.remove(path)
            logger.info("pruned %s", path)
    return len(doomed)


class RuntimeExit(Exception):
    """Fatal, user-facing failure (non-zero exit, no traceback)."""


def run_backup(volumes, backup_dir, image, docker="docker", weekly=False, dry_run=False, now=None):
    """Archive all volumes, optionally emit weekly archives, then prune.

    Returns (failed, succeeded) volume name lists; the caller maps a non-empty
    failure list to a non-zero exit code.
    """
    ensure_image(image, docker=docker, dry_run=dry_run)
    today, weekly_stamp = archive_stamps(now)
    day = now or datetime.date.today()
    emit_weekly = weekly or (day.isoweekday() == 7)
    failed, succeeded = [], []
    for volume in volumes:
        ok, detail = tar_volume(volume, backup_dir, image, docker=docker, dry_run=dry_run)
        if not ok:
            failed.append(volume)
            continue
        if emit_weekly:
            name = archive_name(volume, weekly_stamp)
            path = os.path.join(backup_dir, name)
            if os.path.exists(path):
                logger.info("weekly_already_present volume=%s archive=%s", volume, name)
            elif dry_run:
                logger.info("dry-run: copy %s -> %s", detail, name)
            else:
                shutil.copy2(os.path.join(backup_dir, detail), path)
                logger.info("weekly_created volume=%s archive=%s", volume, name)
        prune_volume(volume, backup_dir, dry_run=dry_run)
        succeeded.append(volume)
    return failed, succeeded


def parse_args(argv=None):
    """Parse the CLI flags (volumes, retention, dry-run, output)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--volumes", default=",".join(DEFAULT_VOLUMES),
                        help="comma-separated named volumes (default: all StackHive volumes)")
    parser.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR, help="archive destination directory")
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="helper image providing tar")
    parser.add_argument("--docker", default="docker", help="docker CLI binary (override for testing)")
    parser.add_argument("--weekly", action="store_true", help="also emit weekly archive stamps")
    parser.add_argument("--dry-run", action="store_true", help="log commands without executing")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def main(argv=None):
    """Entry point: ensure the image, back up each volume, prune; exit 1 on failures."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stderr)
    volumes = [v.strip() for v in args.volumes.split(",") if v.strip()]
    try:
        failed, succeeded = run_backup(
            volumes,
            backup_dir=args.backup_dir,
            image=args.image,
            docker=args.docker,
            weekly=args.weekly,
            dry_run=args.dry_run,
        )
    except RuntimeExit as exc:
        logger.error("%s", exc)
        return 2
    if failed:
        logger.error("backup_incomplete ok=%s failed=%s", ",".join(succeeded), ",".join(failed))
        return 1
    logger.info("backup_complete volumes=%s dir=%s", ",".join(succeeded), args.backup_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
