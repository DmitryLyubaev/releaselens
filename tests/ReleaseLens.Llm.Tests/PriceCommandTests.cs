using System;
using System.Globalization;
using System.IO;
using ReleaseLens.Llm.Providers;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class PriceCommandTests
{
    private const string UsagePrefix = "usage: price";

    [Fact]
    public void Execute_AzureGlobalStandard_PrintsTheCostAtTheReadRate()
    {
        var output = new StringWriter();
        var error = new StringWriter();

        var exitCode = PriceCommand.Execute(
            ["azure-openai", "gpt-4.1-mini", "2025-04-14", "GlobalStandard", "12345", "1024", "678"], output, error);

        // 12,345 x 0.40 + 1,024 x 0.10 + 678 x 1.60 = 6,125.20 per million. The command takes
        // cached tokens before output, the reverse of TokenUsage, so a swap would give 0.0066442.
        Assert.Equal(0, exitCode);
        Assert.Equal(0.0061252m, decimal.Parse(output.ToString(), CultureInfo.InvariantCulture));
        Assert.Equal(string.Empty, error.ToString());
    }

    [Fact]
    public void Execute_IdentityWithNoRate_ExitsOneAndNamesIt()
    {
        var output = new StringWriter();
        var error = new StringWriter();

        var exitCode = PriceCommand.Execute(
            ["azure-openai", "gpt-4.1-mini", "2025-04-14", "DataZoneStandard", "12345", "1024", "678"], output, error);

        Assert.Equal(1, exitCode);
        Assert.Contains("DataZoneStandard", error.ToString(), StringComparison.Ordinal);
        Assert.Equal(string.Empty, output.ToString());
    }

    [Fact]
    public void Execute_DashMeansNoVersionOrType()
    {
        var output = new StringWriter();
        var error = new StringWriter();

        var exitCode = PriceCommand.Execute(
            ["anthropic", "claude-sonnet-5", "-", "-", "1000000", "0", "0"], output, error, new DateOnly(2026, 9, 24));

        Assert.Equal(0, exitCode);
        Assert.Equal(3.00m, decimal.Parse(output.ToString(), CultureInfo.InvariantCulture));
    }

    [Fact]
    public void Execute_WrongArgumentCount_ExitsTwoWithUsage()
    {
        var output = new StringWriter();
        var error = new StringWriter();

        var exitCode = PriceCommand.Execute(
            ["azure-openai", "gpt-4.1-mini", "2025-04-14", "GlobalStandard", "12345", "1024"], output, error);

        Assert.Equal(2, exitCode);
        Assert.StartsWith(UsagePrefix, error.ToString(), StringComparison.Ordinal);
        Assert.Equal(string.Empty, output.ToString());
    }

    [Fact]
    public void Execute_NonNumericTokens_ExitsTwoWithUsage()
    {
        var output = new StringWriter();
        var error = new StringWriter();

        var exitCode = PriceCommand.Execute(
            ["azure-openai", "gpt-4.1-mini", "2025-04-14", "GlobalStandard", "12345", "lots", "678"], output, error);

        Assert.Equal(2, exitCode);
        Assert.StartsWith(UsagePrefix, error.ToString(), StringComparison.Ordinal);
        Assert.Equal(string.Empty, output.ToString());
    }
}
