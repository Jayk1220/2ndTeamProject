from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from dashboard.models import FlightSnapshot
from dashboard.airline import board_for_date, AIRPORT_KOR


class Command(BaseCommand):
    help = "Sync flight schedule for today ~ +7 days (daily)"

    def handle(self, *args, **options):
        start = timezone.localdate()

        total = 0
        for d in range(0, 8):
            target = start + timedelta(days=d)

            for airport_code in AIRPORT_KOR.keys():
                for kind in ("dep", "arr"):
                    rows = board_for_date(airport_code=airport_code, kind=kind, target=target, limit=500)

                    for x in rows:
                        FlightSnapshot.objects.update_or_create(
                            airport_code=airport_code,
                            kind=kind,
                            flight_date=target.strftime("%Y%m%d"),     
                            std=x["std"],
                            flight_no=x.get("flight_no") or "-",
                            origin=x.get("origin") or "-",  # arr에서 들어옴
                            destination=x.get("destination") or "-",  # dep에서 들어옴
                            defaults={
                                "airline": x.get("airline") or "-",
                                "status": x.get("status") or "정상",
                            },
                        )
                        total += 1

        self.stdout.write(self.style.SUCCESS(f"Weekly sync done: {total} upserts"))
