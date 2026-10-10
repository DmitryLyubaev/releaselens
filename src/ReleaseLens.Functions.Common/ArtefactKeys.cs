using System.Buffers.Text;
using System.Globalization;
using System.Text;
using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Functions.Common;

/// <summary>
/// The text form of an artefact's key (<c>issue:123</c>, <c>pull_request:45</c>, <c>commit:&lt;sha&gt;</c>,
/// <c>release:&lt;tag&gt;</c>, the form <see cref="EvidenceKey.ToString"/> writes) and the name of the
/// file that holds the artefact in the <c>artefacts-in</c> container.
/// </summary>
public static class ArtefactKeys
{
    /// <summary>The key a string names, split at the first colon: a wire name, then the value.</summary>
    /// <remarks>
    /// An issue or pull request number must be a positive integer in its plain form (no sign, no leading
    /// zero), so that the key parsed is the key <see cref="EvidenceKey.ToString"/> writes back. A commit's
    /// SHA or a release's tag may be anything non-empty; a tag can hold colons.
    /// </remarks>
    /// <exception cref="FormatException">The text is not a key; the message names it.</exception>
    public static EvidenceKey Parse(string text)
    {
        ArgumentNullException.ThrowIfNull(text);

        var colon = text.IndexOf(':');
        if (colon <= 0 || colon == text.Length - 1)
        {
            throw NotAKey(text);
        }

        EntityType type;
        try
        {
            type = EntityTypeExtensions.FromWireName(text[..colon]);
        }
        catch (ArgumentOutOfRangeException)
        {
            throw NotAKey(text);
        }

        var value = text[(colon + 1)..];
        if (type is EntityType.Issue or EntityType.PullRequest
            && (!int.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out var number)
                || number <= 0
                || number.ToString(CultureInfo.InvariantCulture) != value))
        {
            throw NotAKey(text);
        }

        return new EvidenceKey(type, value);
    }

    /// <summary>
    /// The file's name: the key's string form in base64url without padding, then <c>.json</c>. The key
    /// holds colons, dots and slashes a file name should not.
    /// </summary>
    public static string FileName(EvidenceKey key) =>
        $"{Base64Url.EncodeToString(Encoding.UTF8.GetBytes(key.ToString()))}.json";

    private static FormatException NotAKey(string text) => new(
        $"'{text}' is not an artefact key. Use issue:<number>, pull_request:<number>, commit:<sha> or release:<tag>.");
}
