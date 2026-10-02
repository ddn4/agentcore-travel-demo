"""A tiny in-memory Duffel + Google Places, served through httpx.MockTransport. No network."""

import json

import httpx

START, END = "2027-03-10", "2027-03-13"


def _seg(origin: str, dest: str, day: str) -> dict:
    return {"origin": {"iata_code": origin}, "destination": {"iata_code": dest},
            "departing_at": f"{day}T09:15:00", "arriving_at": f"{day}T21:40:00"}


class FakeDuffel:
    def __init__(self) -> None:
        self.offers = {"off_fake_zz": {
            "id": "off_fake_zz", "total_amount": "412.30", "total_currency": "USD",
            "expires_at": "2099-01-01T00:00:00Z", "owner": {"iata_code": "ZZ", "name": "Duffel Airways"},
            "passengers": [{"id": "pas_1", "type": "adult"}],
            "slices": [{"segments": [_seg("JFK", "LIS", START)]}, {"segments": [_seg("LIS", "JFK", END)]}],
        }}
        self.rates = {"rat_fake_1": {"id": "quo_fake_1", "total_amount": "465.00", "total_currency": "USD",
                                     "check_in_date": START, "check_out_date": END,
                                     "accommodation": {"name": "Memmo Alfama"}}}
        self.places = {"ChIJmuseum": ("Museu Nacional do Azulejo", "museum"),
                       "ChIJfado": ("Clube de Fado", "restaurant")}

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "places.googleapis.com" and path.startswith("/v1/places/"):
            pid = path.rsplit("/", 1)[1]
            if pid not in self.places:
                return httpx.Response(404, json={"error": {"code": 404, "status": "NOT_FOUND"}})
            name, kind = self.places[pid]
            return httpx.Response(200, json={"id": pid, "displayName": {"text": name}, "primaryType": kind})
        if request.method == "GET" and path.startswith("/air/offers/"):
            offer = self.offers.get(path.rsplit("/", 1)[1])
            return self._data(offer) if offer else self._error(404, "not_found")
        if request.method == "POST" and path == "/stays/quotes":
            quote = self.rates.get(json.loads(request.content)["data"]["rate_id"])
            return self._data(quote) if quote else self._error(422, "rate_unavailable")
        return self._error(404, "not_found")

    @staticmethod
    def _data(data: dict) -> httpx.Response:
        return httpx.Response(200, json={"data": data})

    @staticmethod
    def _error(status: int, code: str) -> httpx.Response:
        return httpx.Response(status, json={"errors": [{"code": code, "message": code}]})
