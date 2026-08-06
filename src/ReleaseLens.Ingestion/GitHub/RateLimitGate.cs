namespace ReleaseLens.Ingestion.GitHub;

/// <summary>
/// A token bucket that refills at the configured hourly rate, plus whatever GitHub
/// reports in x-ratelimit-remaining. When the server says zero, we wait for its reset
/// time rather than retrying into a wall.
/// </summary>
public sealed class RateLimitGate
{
    private readonly int _requestsPerHour;
    private readonly double _refillPerSecond;
    private readonly TimeProvider _timeProvider;

    /// <summary>Serialises acquirers. Held across awaits, so it cannot guard shared state.</summary>
    private readonly SemaphoreSlim _mutex = new(1, 1);

    /// <summary>
    /// Guards the bucket state. ObserveHeaders is called from the response path and can run
    /// while an acquirer is waiting, so the fields both touch need a lock of their own —
    /// _mutex only orders acquirers and is held across awaits, which a lock must never be.
    /// </summary>
    private readonly Lock _state = new();

    private double _tokens;
    private DateTimeOffset _lastRefill;
    private DateTimeOffset? _blockedUntil;
    private int _remaining = int.MaxValue;

    public RateLimitGate(int requestsPerHour, TimeProvider timeProvider)
    {
        ArgumentOutOfRangeException.ThrowIfLessThanOrEqual(requestsPerHour, 0);

        _requestsPerHour = requestsPerHour;
        _refillPerSecond = requestsPerHour / 3600.0;
        _timeProvider = timeProvider;
        _tokens = Math.Min(requestsPerHour, 100);
        _lastRefill = timeProvider.GetUtcNow();
    }

    public int Remaining
    {
        get { lock (_state) { return _remaining; } }
    }

    public void ObserveHeaders(int remaining, long resetUnixSeconds)
    {
        lock (_state)
        {
            _remaining = remaining;

            if (remaining <= 0)
            {
                _blockedUntil = DateTimeOffset.FromUnixTimeSeconds(resetUnixSeconds);
            }
        }
    }

    public async Task AcquireAsync(CancellationToken cancellationToken)
    {
        await _mutex.WaitAsync(cancellationToken);
        try
        {
            while (true)
            {
                var now = _timeProvider.GetUtcNow();
                TimeSpan wait;

                lock (_state)
                {
                    if (_blockedUntil is { } until && now < until)
                    {
                        wait = until - now;
                    }
                    else
                    {
                        _blockedUntil = null;

                        var elapsed = (now - _lastRefill).TotalSeconds;
                        if (elapsed > 0)
                        {
                            _tokens = Math.Min(_requestsPerHour, _tokens + (elapsed * _refillPerSecond));
                            _lastRefill = now;
                        }

                        if (_tokens >= 1)
                        {
                            _tokens -= 1;
                            return;
                        }

                        wait = TimeSpan.FromSeconds((1 - _tokens) / _refillPerSecond);
                    }
                }

                await Task.Delay(wait, _timeProvider, cancellationToken);
            }
        }
        finally
        {
            _mutex.Release();
        }
    }
}
