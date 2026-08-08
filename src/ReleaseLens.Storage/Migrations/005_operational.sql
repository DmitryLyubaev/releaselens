create table ingest_checkpoints (
    tenant_id     uuid not null references tenants(tenant_id) on delete cascade,
    entity_type   text not null check (entity_type in ('commit', 'issue', 'pull_request', 'release')),
    cursor_value  text,
    etag          text,
    items_seen    bigint not null default 0,
    updated_at    timestamptz not null default now(),
    primary key (tenant_id, entity_type)
);

create table embedding_dead_letter (
    dead_letter_id   bigint generated always as identity primary key,
    tenant_id        uuid not null references tenants(tenant_id) on delete cascade,
    chunk_id         bigint not null,
    attempts         integer not null default 1,
    last_error       text not null,
    next_attempt_at  timestamptz not null,
    created_at       timestamptz not null default now(),
    unique (chunk_id),
    -- Tenant-scoped, for the same reason as embeddings: FK checks bypass RLS.
    foreign key (tenant_id, chunk_id) references evidence_chunks (tenant_id, chunk_id) on delete cascade
);

create index embedding_dead_letter_due_idx on embedding_dead_letter (next_attempt_at)
    where attempts < 5;

create table token_usage (
    tenant_id   uuid not null references tenants(tenant_id) on delete cascade,
    usage_date  date not null,
    tokens_in   bigint not null default 0,
    tokens_out  bigint not null default 0,
    cost_usd    numeric(12, 6) not null default 0,
    primary key (tenant_id, usage_date)
);
