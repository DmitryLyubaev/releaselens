using System.Text;
using System.Text.RegularExpressions;

namespace ReleaseLens.Core.Chunking;

/// <summary>
/// Cleans raw GitHub markdown before chunking. Fenced code blocks are passed
/// through untouched — whitespace inside them is meaningful and collapsing it
/// makes stack traces and diffs unreadable in a citation.
/// </summary>
public static partial class TextNormaliser
{
    [GeneratedRegex(@"<!--.*?-->", RegexOptions.Singleline)]
    private static partial Regex HtmlComment();

    [GeneratedRegex(@"^#{1,6}\s+", RegexOptions.Multiline)]
    private static partial Regex MarkdownHeading();

    [GeneratedRegex(@"\n{3,}")]
    private static partial Regex ExcessBlankLines();

    private const string Fence = "```";

    public static string Normalise(string? input)
    {
        if (string.IsNullOrWhiteSpace(input))
        {
            return string.Empty;
        }

        var text = input.Replace("\r\n", "\n", StringComparison.Ordinal)
                        .Replace('\r', '\n');

        var builder = new StringBuilder(text.Length);
        var inFence = false;

        foreach (var segment in SplitOnFences(text))
        {
            if (segment.StartsWith(Fence, StringComparison.Ordinal))
            {
                inFence = !inFence;
                builder.Append(segment);
                continue;
            }

            builder.Append(inFence ? segment : CleanProse(segment));
        }

        return builder.ToString().Trim();
    }

    private static IEnumerable<string> SplitOnFences(string text)
    {
        var index = 0;
        while (index < text.Length)
        {
            var fence = text.IndexOf(Fence, index, StringComparison.Ordinal);
            if (fence < 0)
            {
                yield return text[index..];
                yield break;
            }

            if (fence > index)
            {
                yield return text[index..fence];
            }

            yield return Fence;
            index = fence + Fence.Length;
        }
    }

    /// <summary>
    /// Cleans a non-fenced prose segment: strips HTML comments, flattens markdown
    /// headings (dropping the blank line immediately before and after each one),
    /// trims trailing whitespace per line, and collapses runs of 3+ newlines to a
    /// single blank line. Never called on fenced content.
    /// </summary>
    private static string CleanProse(string segment)
    {
        var text = HtmlComment().Replace(segment, string.Empty);
        var lines = text.Split('\n');

        for (var i = 0; i < lines.Length; i++)
        {
            lines[i] = lines[i].TrimEnd();
        }

        var isHeading = new bool[lines.Length];
        for (var i = 0; i < lines.Length; i++)
        {
            var match = MarkdownHeading().Match(lines[i]);
            if (match.Success)
            {
                isHeading[i] = true;
                lines[i] = lines[i][match.Length..];
            }
        }

        var drop = new bool[lines.Length];
        for (var i = 0; i < lines.Length; i++)
        {
            if (!isHeading[i])
            {
                continue;
            }

            if (i > 0 && string.IsNullOrWhiteSpace(lines[i - 1]))
            {
                drop[i - 1] = true;
            }

            if (i < lines.Length - 1 && string.IsNullOrWhiteSpace(lines[i + 1]))
            {
                drop[i + 1] = true;
            }
        }

        var kept = new List<string>(lines.Length);
        for (var i = 0; i < lines.Length; i++)
        {
            if (!drop[i])
            {
                kept.Add(lines[i]);
            }
        }

        var joined = string.Join('\n', kept);
        return ExcessBlankLines().Replace(joined, "\n\n");
    }
}
