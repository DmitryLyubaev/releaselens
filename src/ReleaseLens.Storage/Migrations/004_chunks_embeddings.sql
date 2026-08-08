create table evidence_chunks (
    chunk_id     bigint generated always as identity primary key,
    tenant_id    uuid not null references tenants(tenant_id) on delete cascade,
    entity_type  text not null check (entity_type in ('commit', 'issue', 'pull_request', 'release')),
    entity_key   text not null,
    chunk_index  integer not null,
    content      text not null,
    token_count  integer not null,
    content_tsv  tsvector generated always as (to_tsvector('english', content)) stored,
    created_at   timestamptz not null default now(),
    unique (tenant_id, entity_type, entity_key, chunk_index)
);

create index evidence_chunks_tsv_idx on evidence_chunks using gin (content_tsv);
create index evidence_chunks_entity_idx on evidence_chunks (tenant_id, entity_type, entity_key);

create table embeddings (
    chunk_id    bigint primary key references evidence_chunks(chunk_id) on delete cascade,
    tenant_id   uuid not null references tenants(tenant_id) on delete cascade,
    model       text not null,
    dim         integer not null,
    embedding   vector(384) not null,
    created_at  timestamptz not null default now(),
    constraint embeddings_dim_matches check (dim = 384)
);

create index embeddings_hnsw_idx on embeddings
    using hnsw (embedding vector_cosine_ops) with (m = 16, ef_construction = 64);

create index embeddings_tenant_idx on embeddings (tenant_id);

comment on table embeddings is
    'One row per chunk. vector(384) is BGE-small-en-v1.5. Changing model means a new migration widening the column and a full re-embed.';
