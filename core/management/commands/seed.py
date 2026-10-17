"""Django management command: python manage.py seed --tenant acme  (or --all)."""
from django.core.management.base import BaseCommand, CommandError
from core.seed import seed_tenant

TENANTS = ["acme", "globex"]


class Command(BaseCommand):
    """Seed one or all tenant graphs with baseline nodes and policy memories."""

    help = "Seed FalkorDB tenant graph(s) with sources, tools, agents and policy memories."

    def add_arguments(self, parser):
        """Register --tenant and --all flags."""
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--tenant",
            metavar="NAME",
            help="Seed a single tenant (e.g. acme).",
        )
        group.add_argument(
            "--all",
            action="store_true",
            dest="all_tenants",
            help=f"Seed all tenants: {', '.join(TENANTS)}.",
        )

    def handle(self, *args, **options):
        """Run seed_tenant for the selected tenant(s)."""
        targets = TENANTS if options["all_tenants"] else [options["tenant"]]
        for tenant in targets:
            self.stdout.write(f"Seeding tenant '{tenant}' …")
            try:
                seed_tenant(tenant)
                self.stdout.write(self.style.SUCCESS(f"  ✓ mt_{tenant} seeded."))
            except Exception as exc:
                raise CommandError(f"Failed to seed '{tenant}': {exc}") from exc
