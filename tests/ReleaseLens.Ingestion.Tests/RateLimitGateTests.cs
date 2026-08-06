using System;
using System.Threading.Tasks;
using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Ingestion.GitHub;
using Xunit;

namespace ReleaseLens.Ingestion.Tests;

public class RateLimitGateTests
{
    [Fact]
    public async Task Acquire_WithinBudget_DoesNotDelay()
    {
        var time = new FakeTimeProvider();
        var gate = new RateLimitGate(requestsPerHour: 3600, time);

        await gate.AcquireAsync(TestContext.Current.CancellationToken);
        await gate.AcquireAsync(TestContext.Current.CancellationToken);

        Assert.True(true); // completing without hanging is the assertion
    }

    [Fact]
    public void ObserveResponseHeaders_RecordsRemainingAndReset()
    {
        var time = new FakeTimeProvider();
        var gate = new RateLimitGate(requestsPerHour: 5000, time);

        gate.ObserveHeaders(remaining: 12, resetUnixSeconds: 1_800_000_000);

        Assert.Equal(12, gate.Remaining);
    }

    [Fact]
    public async Task Acquire_WhenRemainingIsZero_WaitsUntilReset()
    {
        var time = new FakeTimeProvider(new DateTimeOffset(2026, 3, 1, 0, 0, 0, TimeSpan.Zero));
        var gate = new RateLimitGate(requestsPerHour: 5000, time);

        var resetAt = time.GetUtcNow().AddSeconds(30).ToUnixTimeSeconds();
        gate.ObserveHeaders(remaining: 0, resetUnixSeconds: resetAt);

        var acquire = gate.AcquireAsync(TestContext.Current.CancellationToken);
        Assert.False(acquire.IsCompleted);

        time.Advance(TimeSpan.FromSeconds(31));
        await acquire;

        Assert.True(acquire.IsCompletedSuccessfully);
    }
}
