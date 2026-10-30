from muda.rate_limit import SlidingWindowLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_limit_then_blocks() -> None:
    limiter = SlidingWindowLimiter(max_events=5, window_s=60, clock=Clock())
    assert [limiter.allow() for _ in range(6)] == [True] * 5 + [False]


def test_window_slides() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(max_events=2, window_s=60, clock=clock)
    assert limiter.allow() and limiter.allow()
    assert not limiter.allow()
    clock.now += 61
    assert limiter.allow()


def test_blocked_attempts_do_not_extend_the_block() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(max_events=1, window_s=60, clock=clock)
    assert limiter.allow()
    for _ in range(50):
        clock.now += 1
        assert not limiter.allow()
    clock.now += 11  # 61s after the only accepted event
    assert limiter.allow()
