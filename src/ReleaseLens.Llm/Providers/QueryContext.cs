namespace ReleaseLens.Llm.Providers;

/// <summary>
/// State that belongs to one query rather than to a provider: which provider answered last,
/// and how much of the query's 429 wait budget is left. <c>QueryAgent</c> creates one per
/// query and puts it on every <see cref="ChatRequest"/>, so the providers and the fallback
/// chain stay stateless singletons and two concurrent queries never share a sticky provider
/// or a budget.
/// </summary>
/// <remarks>
/// Not thread-safe, and it does not need to be: a query's calls run one after another.
/// </remarks>
public sealed class QueryContext(TimeSpan rateLimitWaitBudget)
{
    /// <summary>
    /// The <see cref="IChatProvider.Name"/> of the chain member that answered this query's most
    /// recent call, or null before any has. <see cref="FallbackChatProvider"/> reads and writes it.
    /// </summary>
    public string? LastProvider { get; set; }

    public TimeSpan RateLimitWaitRemaining { get; private set; } = rateLimitWaitBudget;

    /// <summary>
    /// Spends <paramref name="wait"/> from the budget if all of it fits, and reports whether it
    /// did. A wait that does not fit leaves the budget untouched: starting a wait the query
    /// cannot afford to finish buys nothing.
    /// </summary>
    /// <exception cref="ArgumentOutOfRangeException"><paramref name="wait"/> is negative.</exception>
    public bool TrySpendWait(TimeSpan wait)
    {
        // A negative wait would refill the budget. A caller treats an unusable retry header as
        // absent before it gets here, so a negative value reaching this is a caller bug.
        ArgumentOutOfRangeException.ThrowIfLessThan(wait, TimeSpan.Zero);

        if (wait > RateLimitWaitRemaining)
        {
            return false;
        }

        RateLimitWaitRemaining -= wait;
        return true;
    }
}
