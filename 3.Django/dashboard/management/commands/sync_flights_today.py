from django.core.management.base import BaseCommand
from django.utils import timezone

from dashboard.models import FlightSnapshot
from dashboard.airline import get_board, AIRPORT_KOR


class Command(BaseCommand):
    help = "Sync today's flight status (delay/cancel) every 5 minutes"

    def handle(self, *args, **options):
        today = timezone.localdate().strftime("%Y%m%d")
        total = 0

        for airport_code in AIRPORT_KOR.keys():
            for kind in ("dep", "arr"):
                rows = get_board(airport_code=airport_code, kind=kind, limit=500)

                for x in rows:
                    std = (x.get("time") or "").replace(":", "")
                    if len(std) != 4:
                        continue

                    FlightSnapshot.objects.update_or_create(
                        airport_code=airport_code,
                        kind=kind,
                        flight_date=today,
                        std=std,
                        flight_no=x.get("flight") or "-",
                        origin=x.get("origin") or "-",
                        destination=x.get("destination") or "-",
                        defaults={
                            "airline": x.get("airline") or "-",
                            "status": x.get("status") or "정상",
                        },
                    )
                    total += 1

        # 오늘 이전 데이터 정리
        FlightSnapshot.objects.filter(flight_date__lt=today).delete()

        self.stdout.write(self.style.SUCCESS(f"Today sync done: {total} upserts"))
