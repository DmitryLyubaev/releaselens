namespace ReleaseLens.Ingestion.GitHub;

/// <summary>
/// A token bucket that refills at the configured hourly rate, plus whatever GitHub
/// reports in x-ratelimit-remaining. When the server says zero, we wait for its reset
/// time rather than retrying into a wall.
/// </summary>
public sealed class RateLimitGate(int requestsPerHour, TimeProvider timeProvider)
{
    private readonly double _refillPerSecond = requestsPerHour / 3600.0;
    private readonly SemaphoreSlim _mutex = new(1, 1);

    private double _tokens = Math.Min(requestsPerHour, 100);
    private DateTimeOffset _lastRefill = timeProvider.GetUtcNow();
    private DateTimeOffset? _blockedUntil;

    public int Remaining { get; private set; } = int.MaxValue;

    public void ObserveHeaders(int remaining, long resetUnixSeconds)
    {
        Remaining = remaining;

        if (remaining <= 0)
        {
            _blockedUntil = DateTimeOffset.FromUnixTimeSeconds(resetUnixSeconds);
        }
    }

    public async Task AcquireAsync(CancellationToken cancellationToken)
    {
        await _mutex.WaitAsync(cancellationToken);
        try
        {
            while (true)
            {
                var now = timeProvider.GetUtcNow();

                if (_blockedUntil is { } until && now < until)
                {
                    await Task.Delay(until - now, timeProvider, cancellationToken);
                    continue;
                }

                _blockedUntil = null;

                var elapsed = (now - _lastRefill).TotalSeconds;
                if (elapsed > 0)
                {
                    _tokens = Math.Min(requestsPerHour, _tokens + (elapsed * _refillPerSecond));
                    _lastRefill = now;
                }

                if (_tokens >= 1)
                {
                    _tokens -= 1;
                    return;
                }

                var secondsUntilNextToken = (1 - _tokens) / _refillPerSecond;
                await Task.Delay(TimeSpan.FromSeconds(secondsUntilNextToken), timeProvider, cancellationToken);
            }
        }
        finally
        {
            _mutex.Release();
        }
    }
}
