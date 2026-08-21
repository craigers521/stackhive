"""Backup script logic: retention selection, naming, and pruning (T065)."""
import datetime

import pytest

from scripts.backup_volumes import (
    DAILY_KEEP,
    WEEKLY_KEEP,
    archive_name,
    archive_stamps,
    is_weekly_stamp,
    prune_volume,
    select_to_delete,
)


class TestRetentionSelection:
    """Oldest-archive selection for retention pruning."""
    def test_under_keep_keeps_everything(self):
        """Nothing is pruned while all archives are within retention."""
        names = [f"stackhive-db-2026-08-{d:02d}.tar.gz" for d in range(1, DAILY_KEEP)]
        assert select_to_delete(names, DAILY_KEEP) == []

    def test_excess_deletes_oldest(self):
        """Excess archives are pruned oldest-first."""
        names = [f"stackhive-db-2026-08-{d:02d}.tar.gz" for d in range(1, DAILY_KEEP + 3)]
        doomed = select_to_delete(names, DAILY_KEEP)
        assert len(doomed) == 2
        assert all(d.startswith("stackhive-db-2026-08-0") for d in doomed)
        newest = f"stackhive-db-2026-08-{DAILY_KEEP + 2:02d}.tar.gz"
        assert newest not in doomed


class TestNaming:
    """Archive filename and stamp conventions."""
    def test_archive_name(self):
        """Daily and weekly archive names follow the documented scheme."""
        assert archive_name("gitlab-data", "2026-08-19") == "stackhive-gitlab-data-2026-08-19.tar.gz"

    def test_stamps_daily_and_weekly(self):
        """Stamps parse back to dates, weekly using the ISO week stamp."""
        day = datetime.date(2026, 8, 19)  # a Wednesday
        daily, weekly = archive_stamps(day)
        assert daily == "2026-08-19"
        assert weekly == f"{day.isocalendar()[0]}-W{day.isocalendar()[1]:02d}"

    def test_is_weekly_stamp(self):
        """Only ISO-week stamps classify as weekly."""
        assert is_weekly_stamp("2026-W34")
        assert not is_weekly_stamp("2026-08-19")


class TestPruning:
    """prune_volume deletes only the excess archives of one volume."""
    def test_prunes_beyond_retention(self, tmp_path):
        """Excess archives beyond retention are deleted oldest-first."""
        volume = "grafana-data"
        for day in range(1, DAILY_KEEP + 4):
            (tmp_path / archive_name(volume, f"2026-08-{day:02d}")).write_text("x")
        for week in range(1, WEEKLY_KEEP + 2):
            (tmp_path / archive_name(volume, f"2026-W{week:02d}")).write_text("x")

        removed = prune_volume(volume, str(tmp_path))
        remaining = sorted(p.name for p in tmp_path.iterdir())
        assert removed == 3 + 1
        assert len(remaining) == DAILY_KEEP + WEEKLY_KEEP
        assert archive_name(volume, "2026-08-01") not in remaining
        assert archive_name(volume, "2026-W01") not in remaining
        assert archive_name(volume, "2026-08-10") in remaining
        assert archive_name(volume, "2026-W05") in remaining

    def test_ignores_other_volumes(self, tmp_path):
        """Archives belonging to other volumes are untouched."""
        for day in range(1, DAILY_KEEP + 3):
            (tmp_path / archive_name("influxdb-data", f"2026-08-{day:02d}")).write_text("x")
        removed = prune_volume("grafana-data", str(tmp_path))
        assert removed == 0
        assert len(list(tmp_path.iterdir())) == DAILY_KEEP + 2
