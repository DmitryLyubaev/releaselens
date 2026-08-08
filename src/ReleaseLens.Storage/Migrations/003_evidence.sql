create table commits (
    tenant_id     uuid not null references tenants(tenant_id) on delete cascade,
    sha           text not null,
    message       text not null,
    author_name   text,
    author_email  text,
    authored_at   timestamptz not null,
    committed_at  timestamptz not null,
    url           text not null,
    primary key (tenant_id, sha)
);

create index commits_committed_at_idx on commits (tenant_id, committed_at desc);

create table files_changed (
    tenant_id  uuid not null,
    sha        text not null,
    path       text not null,
    status     text not null,
    additions  integer not null default 0,
    deletions  integer not null default 0,
    primary key (tenant_id, sha, path),
    foreign key (tenant_id, sha) references commits(tenant_id, sha) on delete cascade
);

create index files_changed_path_idx on files_changed (tenant_id, path);
create index files_changed_path_trgm_idx on files_changed using gin (path gin_trgm_ops);

create table issues (
    tenant_id   uuid not null references tenants(tenant_id) on delete cascade,
    number      integer not null,
    title       text not null,
    body        text not null default '',
    state       text not null,
    labels      text[] not null default '{}',
    author      text,
    created_at  timestamptz not null,
    closed_at   timestamptz,
    url         text not null,
    primary key (tenant_id, number)
);

create index issues_labels_idx on issues using gin (labels);
create index issues_created_at_idx on issues (tenant_id, created_at desc);

create table pull_requests (
    tenant_id         uuid not null references tenants(tenant_id) on delete cascade,
    number            integer not null,
    title             text not null,
    body              text not null default '',
    state             text not null,
    merged_at         timestamptz,
    merge_commit_sha  text,
    base_ref          text not null,
    head_ref          text not null,
    author            text,
    created_at        timestamptz not null,
    url               text not null,
    primary key (tenant_id, number)
);

create index pull_requests_merged_at_idx on pull_requests (tenant_id, merged_at desc);
create index pull_requests_merge_sha_idx on pull_requests (tenant_id, merge_commit_sha);

create table releases (
    tenant_id         uuid not null references tenants(tenant_id) on delete cascade,
    tag               text not null,
    name              text,
    body              text not null default '',
    published_at      timestamptz,
    target_commitish  text,
    url               text not null,
    primary key (tenant_id, tag)
);

create index releases_published_at_idx on releases (tenant_id, published_at desc);
