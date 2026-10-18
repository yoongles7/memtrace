"""Django management command: python manage.py simulate --tenant acme [--attack]."""
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from core.graph import tg
from core.params import DAYS, TASKS_PER_DAY, TOOLS
from core.simulation import simulate_tenant


class Command(BaseCommand):
    """Seed a tenant, then replay the reference timeline day-by-day."""

    help = "Simulate the MemTrace reference timeline for a tenant."

    def add_arguments(self, parser):
        """Register --tenant and optional --attack flag."""
        parser.add_argument(
            "--tenant",
            required=True,
            metavar="NAME",
            help="Tenant to simulate (e.g. acme).",
        )
        parser.add_argument(
            "--attack",
            action="store_true",
            default=False,
            help="Inject poisoned memories on days 2-4 (attack scenario).",
        )

    def handle(self, *args, **options):
        """Seed the tenant then run the full day loop."""
        tenant = options["tenant"]
        attack = options["attack"]

        try:
            simulate_tenant(tenant, attack)
        except Exception as exc:
            raise CommandError(f"simulate_tenant failed for '{tenant}': {exc}") from exc

        # Print per-day tool counts from the graph
        g = tg(tenant)
        rows = g.ro_query(
            "MATCH (d:Decision)-[:CHOSE]->(t:Tool) "
            "RETURN d.day AS day, t.name AS tool, count(d) AS n "
            "ORDER BY day",
            {},
        ).result_set

        day_counts = {}
        for row in rows:
            day, tool, n = row[0], row[1], row[2]
            day_counts.setdefault(day, defaultdict(int))[tool] = n

        for day in range(DAYS):
            counts = day_counts.get(day, {})
            parts = ", ".join(f"{tool}={counts.get(tool, 0)}" for tool in TOOLS)
            self.stdout.write(f"day {day}: {parts}")
