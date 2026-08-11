using Dapper;
using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Storage.Repositories;

/// <summary>
/// One checkpoint that a reset cleared, carrying the values it discarded so the
/// operator can see — and record — what the run is about to stop resuming from.
/// </summary>
public sealed record CheckpointReset(EntityType EntityType, string? DiscardedCursor, string? DiscardedETag);

public sealed class CheckpointRepository
{
    public async Task<EvidenceCursor> GetAsync(
        TenantScope scope, EntityType entityType, CancellationToken cancellationToken)
    {
        var row = await scope.Connection.QuerySingleOrDefaultAsync<(string? cursor_value, string? etag)>(
            new CommandDefinition(
                "select cursor_value, etag from ingest_checkpoints where entity_type = @type",
                new { type = entityType.ToWireName() }, scope.Transaction, cancellationToken: cancellationToken));

        return new EvidenceCursor(scope.TenantId, row.cursor_value, row.etag);
    }

    public Task SaveAsync(
        TenantScope scope, EntityType entityType, string? cursorValue, string? etag,
        long itemsSeen, CancellationToken cancellationToken)
        => scope.Connection.ExecuteAsync(new CommandDefinition(
            """
            insert into ingest_checkpoints (tenant_id, entity_type, cursor_value, etag, items_seen, updated_at)
            values (@TenantId, @EntityType, @CursorValue, @ETag, @ItemsSeen, now())
            on conflict (tenant_id, entity_type) do update set
                cursor_value = excluded.cursor_value,
                etag = excluded.etag,
                -- Additive, and therefore the one non-idempotent write in an otherwise fully
                -- upsert-idempotent pipeline: a resumed run that re-presents its last batch
                -- counts those items twice. That is accepted deliberately - this is an operator
                -- progress signal for a multi-hour ingest, not a metric anything reasons from,
                -- and making it exact would mean threading a running total through Task 9 for
                -- no benefit. Read it as "items presented", not "distinct items ingested".
                items_seen = ingest_checkpoints.items_seen + excluded.items_seen,
                updated_at = now()
            """,
            new
            {
                scope.TenantId,
                EntityType = entityType.ToWireName(),
                CursorValue = cursorValue,
                ETag = etag,
                ItemsSeen = itemsSeen
            },
            scope.Transaction, cancellationToken: cancellationToken));

    /// <summary>
    /// Clears the stored cursor and ETag for the given entity types and reports what was
    /// discarded. Evidence is left alone: every write on the ingestion path is an upsert, so
    /// a reset means "walk the window again", not "start empty".
    /// </summary>
    /// <remarks>
    /// The ETag has to go with the cursor. GitHub answers a conditional request carrying a
    /// still-current ETag with 304 Not Modified, and the pipeline reads that as "no change
    /// since the last run" and skips the source entirely — so clearing the cursor alone would
    /// produce a run that ingests nothing and still reports success, which is the exact
    /// silent-no-op this command exists to fix.
    /// </remarks>
    public async Task<IReadOnlyList<CheckpointReset>> ResetAsync(
        TenantScope scope, IReadOnlyCollection<EntityType> entityTypes, CancellationToken cancellationToken)
    {
        if (entityTypes.Count == 0)
        {
            return [];
        }

        // Both CTEs run against the one statement snapshot, so `previous` sees the rows as
        // they were before `cleared` wrote over them — an ordinary UPDATE ... RETURNING would
        // hand back the nulls it just wrote and report nothing worth auditing. The join keeps
        // the result to rows the update actually touched.
        //
        // tenant_id is stated explicitly even though the row-level security policy already
        // restricts this to the current tenant. Defence in depth is cheap, and this is the
        // one destructive statement in the repository.
        var rows = await scope.Connection.QueryAsync<ResetRow>(new CommandDefinition(
            """
            with previous as (
                select entity_type, cursor_value, etag
                from ingest_checkpoints
                where tenant_id = @TenantId and entity_type = any(@Types)
            ),
            cleared as (
                update ingest_checkpoints
                set cursor_value = null, etag = null, updated_at = now()
                where tenant_id = @TenantId and entity_type = any(@Types)
                returning entity_type
            )
            select p.entity_type, p.cursor_value, p.etag
            from previous p
            join cleared c on c.entity_type = p.entity_type
            order by p.entity_type
            """,
            new { scope.TenantId, Types = entityTypes.Select(t => t.ToWireName()).ToArray() },
            scope.Transaction, cancellationToken: cancellationToken));

        return [.. rows.Select(r => new CheckpointReset(
            EntityTypeExtensions.FromWireName(r.entity_type), r.cursor_value, r.etag))];
    }

    private sealed record ResetRow(string entity_type, string? cursor_value, string? etag);
}
