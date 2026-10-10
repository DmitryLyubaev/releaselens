using System.Buffers.Text;
using System.Globalization;
using System.Text;

namespace ReleaseLens.Functions.Common;

/// <summary>
/// The rule for a chunk's key in the index. An AI Search key allows only letters, digits, underscore,
/// dash and equals, while an artefact ID holds colons and dots, so the artefact is base64url-encoded.
/// Sending the same artefact twice writes the same keys, which makes Event Grid's at-least-once
/// delivery harmless.
/// </summary>
public static class ChunkKeys
{
    /// <summary>The key of chunk <paramref name="index"/> of <paramref name="artefact"/>: base64url without padding, a dash, the index.</summary>
    public static string For(string artefact, int index) =>
        $"{Base64Url.EncodeToString(Encoding.UTF8.GetBytes(artefact))}-{index.ToString(CultureInfo.InvariantCulture)}";

    /// <summary>
    /// Every key in <paramref name="existing"/> that is not in <paramref name="fresh"/>: what an artefact
    /// that came back shorter leaves behind, and any key written under another scheme (the
    /// bulk-loaded corpus uses numeric keys).
    /// </summary>
    public static IReadOnlyList<string> Stale(IEnumerable<string> existing, IReadOnlySet<string> fresh) =>
        [.. existing.Where(key => !fresh.Contains(key))];
}
