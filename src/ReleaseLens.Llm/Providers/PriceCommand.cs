using System.Globalization;

namespace ReleaseLens.Llm.Providers;

/// <summary>
/// Prices one call from the command line with <see cref="ModelPricing"/>, so the deploy smoke
/// test can recompute a recorded cost from the app's own rates instead of a second copy of them.
/// </summary>
/// <remarks>
/// <c>inputTokens</c> is uncached input, as every provider reports it; <c>cachedTokens</c> is
/// priced separately at the cached rate. Cache writes are always zero: the command exists to check
/// Azure OpenAI calls, and the OpenAI wire reports no cache writes.
/// </remarks>
public static class PriceCommand
{
    private const string Usage =
        "usage: price <provider> <model> <version|-> <deploymentType|-> <inputTokens> <cachedTokens> <outputTokens>";

    /// <returns>0 with the cost printed, 1 when the identity has no rate, 2 for bad arguments.</returns>
    public static int Execute(IReadOnlyList<string> args, TextWriter output, TextWriter error, DateOnly? asOf = null)
    {
        if (args.Count != 7
            || !TryParseTokens(args[4], out var input)
            || !TryParseTokens(args[5], out var cached)
            || !TryParseTokens(args[6], out var outputTokens))
        {
            error.WriteLine(Usage);
            return 2;
        }

        var identity = new PricingIdentity(args[0], args[1], NullIfDash(args[2]), NullIfDash(args[3]));
        var usage = new TokenUsage(input, outputTokens, cached, 0);

        decimal cost;
        try
        {
            cost = ModelPricing.CostUsd(identity, usage, asOf ?? DateOnly.FromDateTime(DateTime.UtcNow));
        }
        catch (InvalidOperationException exception)
        {
            error.WriteLine(exception.Message);
            return 1;
        }

        output.WriteLine(cost.ToString(CultureInfo.InvariantCulture));
        return 0;
    }

    // NumberStyles.None: a token count is digits only, so a sign, separator or space is a bad argument.
    private static bool TryParseTokens(string value, out int tokens)
        => int.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out tokens);

    private static string? NullIfDash(string value) => value == "-" ? null : value;
}
