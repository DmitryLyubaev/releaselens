using System.Globalization;
using System.Runtime.CompilerServices;
using Dapper;
using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Storage.Retrieval;

/// <summary>One chunk as the retrieval benchmark sees it. <see cref="Artefact"/> is <c>type:key</c>.</summary>
public sealed record ExportedChunk(
    long ChunkId, string Artefact, string EntityType, string EntityKey, int ChunkIndex, int TokenCount, string Content);

/// <summary>A merged pull request and its merge commit, both in <c>type:key</c> form.</summary>
public sealed record ExportedLink(string PullRequest, string Commit);

/// <summary>
/// Reads the tenant's corpus out for the retrieval benchmark: every chunk, and the links
/// between pull requests and their merge commits that lenient scoring accepts as the same
/// change. RLS scopes both to the tenant on the connection.
/// </summary>
public sealed class CorpusExporter
{
    /// <summary>
    /// Every chunk of the tenant, embedded or not, in <c>chunk_id</c> order. Streamed rather
    /// than buffered, because the full corpus is tens of thousands of chunks.
    /// </summary>
    public async IAsyncEnumerable<ExportedChunk> ExportChunksAsync(
        TenantScope scope, [EnumeratorCancellation] CancellationToken cancellationToken)
    {
        var rows = scope.Connection.QueryUnbufferedAsync<ChunkRow>(
            """
            select chunk_id    as ChunkId,
                   entity_type as EntityTypeWire,
                   entity_key  as EntityKey,
                   chunk_index as ChunkIndex,
                   token_count as TokenCount,
                   content     as Content
            from evidence_chunks
            order by chunk_id
            """,
            transaction: scope.Transaction);

        await foreach (var r in rows.WithCancellation(cancellationToken))
        {
            yield return new ExportedChunk(
                r.ChunkId,
                Artefact(EntityTypeExtensions.FromWireName(r.EntityTypeWire), r.EntityKey),
                r.EntityTypeWire,
                r.EntityKey,
                r.ChunkIndex,
                r.TokenCount,
                r.Content);
        }
    }

    /// <summary>
    /// One link per merged pull request whose <c>merge_commit_sha</c> names a commit in the
    /// corpus. <c>merged_at</c> is required as well as the SHA: GitHub reports a
    /// <c>merge_commit_sha</c> for an unmerged pull request too (its test merge), and that
    /// commit is not the change the pull request made.
    /// </summary>
    public async Task<IReadOnlyList<ExportedLink>> ExportLinksAsync(
        TenantScope scope, CancellationToken cancellationToken)
    {
        var rows = await scope.Connection.QueryAsync<LinkRow>(new CommandDefinition(
            """
            select p.number as Number,
                   c.sha    as Sha
            from pull_requests p
            join commits c on c.tenant_id = p.tenant_id and c.sha = p.merge_commit_sha
            where p.merged_at is not null
            order by p.number
            """,
            transaction: scope.Transaction, cancellationToken: cancellationToken));

        return [.. rows.Select(r => new ExportedLink(
            Artefact(EntityType.PullRequest, r.Number.ToString(CultureInfo.InvariantCulture)),
            Artefact(EntityType.Commit, r.Sha)))];
    }

    internal static string Artefact(EntityType type, string entityKey) => $"{type.ToWireName()}:{entityKey}";

    private sealed record ChunkRow(
        long ChunkId, string EntityTypeWire, string EntityKey, int ChunkIndex, int TokenCount, string Content);

    private sealed record LinkRow(int Number, string Sha);
}
