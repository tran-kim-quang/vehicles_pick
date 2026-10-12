from order_vehicles.clients.python.turns import TurnController


class NetworkSpy:
    calls = 0

    def send(self, _data):
        self.calls += 1


def test_no_network_calls_inside_callback() -> None:
    spy = NetworkSpy()
    turns = TurnController(type("VAD", (), {"is_speech": lambda self, pcm: True})())
    for _ in range(10):
        turns.feed(b"frame")
    assert spy.calls == 0
