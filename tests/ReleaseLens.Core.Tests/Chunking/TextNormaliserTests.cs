using ReleaseLens.Core.Chunking;
using Xunit;

namespace ReleaseLens.Core.Tests.Chunking;

public class TextNormaliserTests
{
    [Fact]
    public void Normalise_CollapsesWindowsLineEndings()
    {
        Assert.Equal("a\nb", TextNormaliser.Normalise("a\r\nb"));
    }

    [Fact]
    public void Normalise_CollapsesThreeOrMoreBlankLinesToOne()
    {
        Assert.Equal("a\n\nb", TextNormaliser.Normalise("a\n\n\n\n\nb"));
    }

    [Fact]
    public void Normalise_StripsHtmlComments()
    {
        Assert.Equal("visible", TextNormaliser.Normalise("<!-- template boilerplate -->visible"));
    }

    [Fact]
    public void Normalise_RemovesGitHubIssueTemplateHeadings()
    {
        const string input = "### Describe the bug\n\nIt crashes.\n\n### Expected behavior\n\nIt should not.";
        Assert.Equal("Describe the bug\nIt crashes.\nExpected behavior\nIt should not.", TextNormaliser.Normalise(input));
    }

    [Fact]
    public void Normalise_PreservesFencedCodeBlocksVerbatim()
    {
        const string input = "before\n\n```csharp\nvar x =  1;\n\n\nvar y = 2;\n```\n\nafter";
        var result = TextNormaliser.Normalise(input);
        Assert.Contains("var x =  1;\n\n\nvar y = 2;", result);
    }

    [Fact]
    public void Normalise_TrimsTrailingWhitespaceOnEachLine()
    {
        Assert.Equal("a\nb", TextNormaliser.Normalise("a   \nb\t"));
    }

    [Fact]
    public void Normalise_HandlesNullAndWhitespaceAsEmpty()
    {
        Assert.Equal(string.Empty, TextNormaliser.Normalise(null));
        Assert.Equal(string.Empty, TextNormaliser.Normalise("   \n  \n "));
    }
}
