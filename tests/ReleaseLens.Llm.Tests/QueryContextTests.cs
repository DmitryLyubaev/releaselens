using System;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class QueryContextTests
{
    [Fact]
    public void New_HasTheWholeBudget_AndNoProvider()
    {
        var query = new QueryContext(TimeSpan.FromSeconds(3));

        Assert.Equal(TimeSpan.FromSeconds(3), query.RateLimitWaitRemaining);
        Assert.Null(query.LastProvider);
    }

    [Fact]
    public void TrySpendWait_ThatFits_SpendsIt()
    {
        var query = new QueryContext(TimeSpan.FromSeconds(3));

        Assert.True(query.TrySpendWait(TimeSpan.FromMilliseconds(1200)));
        Assert.Equal(TimeSpan.FromMilliseconds(1800), query.RateLimitWaitRemaining);
    }

    [Fact]
    public void TrySpendWait_OfExactlyWhatRemains_SpendsItAll()
    {
        var query = new QueryContext(TimeSpan.FromSeconds(3));

        Assert.True(query.TrySpendWait(TimeSpan.FromSeconds(3)));
        Assert.Equal(TimeSpan.Zero, query.RateLimitWaitRemaining);
    }

    [Fact]
    public void TrySpendWait_ThatDoesNotFit_SpendsNothing()
    {
        var query = new QueryContext(TimeSpan.FromSeconds(3));
        Assert.True(query.TrySpendWait(TimeSpan.FromSeconds(2)));

        Assert.False(query.TrySpendWait(TimeSpan.FromMilliseconds(1001)));
        Assert.Equal(TimeSpan.FromSeconds(1), query.RateLimitWaitRemaining);
    }

    [Fact]
    public void TrySpendWait_Negative_Throws_AndSpendsNothing()
    {
        var query = new QueryContext(TimeSpan.FromSeconds(3));

        Assert.Throws<ArgumentOutOfRangeException>(() => query.TrySpendWait(TimeSpan.FromMilliseconds(-1)));
        Assert.Equal(TimeSpan.FromSeconds(3), query.RateLimitWaitRemaining);
    }
}
