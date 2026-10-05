-- Artsy crawl: normalized schema.
-- Source: the Relay GraphQL cache (__RELAY_HYDRATION_DATA__) embedded in each HTML page.
-- Artsy ids are 24-char hex strings ("internalID"); artworks are keyed by their URL slug,
-- because artist pages only expose artwork slugs.
-- Safe to run repeatedly.

CREATE TABLE IF NOT EXISTS crawl_pages (
    filename        text PRIMARY KEY,
    crawl_folder    text,
    url             text,
    canonical_url   text,
    page_type       text,            -- artist | artwork | unknown
    artist_id       text,
    artwork_slug    text,
    status_code     int,
    error           text,
    block_detected  boolean,
    block_reason    text,
    duration_ms     int,
    crawled_at      timestamptz,
    graphql_errors  jsonb,           -- errors Artsy's API returned while rendering the page
    parse_status    text,            -- ok | skipped | error
    parse_error     text,
    loaded_at       timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crawl_pages_folder_idx ON crawl_pages(crawl_folder);

-- Raw GraphQL responses, so any field not modelled below can still be queried.
CREATE TABLE IF NOT EXISTS raw_pages (
    filename   text PRIMARY KEY REFERENCES crawl_pages ON DELETE CASCADE,
    page_type  text,
    state      jsonb
);

-- Shared entities ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS genes (           -- Artsy's categories: medium, movement, period, ...
    gene_id  text PRIMARY KEY,
    slug     text,
    name     text
);

CREATE TABLE IF NOT EXISTS partners (        -- galleries, auction houses, institutions
    partner_id    text PRIMARY KEY,
    slug          text,
    name          text,
    url           text,
    icon_url      text,
    cities        text[],
    is_inquireable boolean
);
CREATE INDEX IF NOT EXISTS partners_slug_idx ON partners(slug);

CREATE TABLE IF NOT EXISTS articles (
    article_id     text PRIMARY KEY,
    title          text,
    byline         text,
    url            text,
    published_at   date,
    thumbnail_url  text
);

-- Artists --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artists (
    artist_id              text PRIMARY KEY,   -- Artsy internalID
    slug                   text UNIQUE,
    name                   text,
    url                    text,
    gender                 text,
    nationality            text,
    birthday               text,               -- as given ("1961", "c. 1650", ...)
    deathday               text,
    birth_year             int,
    death_year             int,
    hometown               text,
    nationality_and_dates  text,               -- e.g. "German, b. 1961"
    alternate_names        text[],
    awards                 text,
    biography              text,               -- plain text
    biography_html         text,
    biography_credit       text,               -- e.g. "Submitted by <gallery>"
    biography_credit_url   text,
    follows_count          int,
    articles_count         int,
    cover_artwork_slug     text,
    cover_image_url        text,
    cover_image_width      int,
    cover_image_height     int,
    meta_title             text,
    meta_description       text,
    og_image_url           text,
    has_page               boolean,            -- true when the artist's own page was crawled
    source_file            text,
    crawled_at             timestamptz,
    updated_at             timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS artists_nationality_idx ON artists(nationality);
CREATE INDEX IF NOT EXISTS artists_birth_year_idx ON artists(birth_year);

CREATE TABLE IF NOT EXISTS artist_genes (
    artist_id  text REFERENCES artists ON DELETE CASCADE,
    gene_id    text REFERENCES genes,
    kind       text,                            -- category | medium | movement
    position   int,
    PRIMARY KEY (artist_id, gene_id, kind)
);
CREATE INDEX IF NOT EXISTS artist_genes_gene_idx ON artist_genes(gene_id);

-- Galleries verified as representing the artist
CREATE TABLE IF NOT EXISTS artist_representatives (
    artist_id   text REFERENCES artists ON DELETE CASCADE,
    partner_id  text REFERENCES partners,
    position    int,
    PRIMARY KEY (artist_id, partner_id)
);
CREATE INDEX IF NOT EXISTS artist_representatives_partner_idx ON artist_representatives(partner_id);

-- Career highlights: solo/group shows, collections, biennials, reviews, awards, ...
CREATE TABLE IF NOT EXISTS artist_insights (
    artist_id    text REFERENCES artists ON DELETE CASCADE,
    kind         text,
    label        text,
    description  text,
    entities     text[],                        -- institutions / publications / awards named
    position     int,
    PRIMARY KEY (artist_id, kind)
);
CREATE INDEX IF NOT EXISTS artist_insights_entities_idx ON artist_insights USING gin(entities);

CREATE TABLE IF NOT EXISTS artist_articles (
    artist_id   text REFERENCES artists ON DELETE CASCADE,
    article_id  text REFERENCES articles,
    position    int,
    PRIMARY KEY (artist_id, article_id)
);

-- Artworks -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artworks (
    slug                    text PRIMARY KEY,
    artwork_id              text,               -- internalID (artwork pages only)
    artist_id               text REFERENCES artists ON DELETE SET NULL,
    title                   text,
    date                    text,               -- as given ("2017", "ca. 1920", ...)
    year                    int,
    url                     text,
    artist_names            text,
    category                text,
    medium                  text,
    medium_type             text,
    attribution_class       text,               -- unique | limited edition | open edition ...
    series                  text,
    dimensions_in           text,
    dimensions_cm           text,
    width_cm                numeric,
    height_cm               numeric,
    depth_cm                numeric,
    diameter_cm             numeric,
    is_edition              boolean,
    edition_of              text,
    price_display           text,
    price_amount            numeric,            -- in price_currency
    price_currency          text,
    sale_message            text,
    availability            text,
    is_sold                 boolean,
    is_acquireable          boolean,
    is_offerable            boolean,
    is_inquireable          boolean,
    is_in_auction           boolean,
    is_biddable             boolean,
    is_framed               boolean,
    framed_details          text,
    signature               text,
    has_certificate_of_authenticity boolean,
    condition_description   text,
    publisher               text,
    manufacturer            text,
    image_rights            text,
    provenance              text,
    exhibition_history      text,
    literature              text,
    description_html        text,
    additional_info_html    text,
    shipping_origin         text,
    shipping_info           text,
    pickup_available        boolean,
    price_includes_tax      text,
    partner_id              text REFERENCES partners,
    visibility_level        text,
    is_published            boolean,
    curators_pick           boolean,
    increased_interest      boolean,
    image_url               text,
    image_width             int,
    image_height            int,
    meta_title              text,
    meta_description        text,
    has_detail              boolean,            -- true when the artwork's own page was crawled
    source_file             text,
    crawled_at              timestamptz,
    updated_at              timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS artworks_artist_idx ON artworks(artist_id);
CREATE INDEX IF NOT EXISTS artworks_partner_idx ON artworks(partner_id);
CREATE INDEX IF NOT EXISTS artworks_year_idx ON artworks(year);

-- "Notable works" listed on an artist page, in display order
CREATE TABLE IF NOT EXISTS artist_notable_artworks (
    artist_id     text REFERENCES artists ON DELETE CASCADE,
    artwork_slug  text REFERENCES artworks ON DELETE CASCADE,
    position      int,
    PRIMARY KEY (artist_id, artwork_slug)
);

CREATE TABLE IF NOT EXISTS artwork_artists (
    artwork_slug  text REFERENCES artworks ON DELETE CASCADE,
    artist_id     text REFERENCES artists ON DELETE CASCADE,
    position      int,
    PRIMARY KEY (artwork_slug, artist_id)
);

CREATE TABLE IF NOT EXISTS artwork_images (
    artwork_slug  text REFERENCES artworks ON DELETE CASCADE,
    position      int,
    image_id      text,
    url           text,
    is_default    boolean,
    width         int,
    height        int,
    PRIMARY KEY (artwork_slug, position)
);

CREATE TABLE IF NOT EXISTS artwork_edition_sets (
    artwork_slug    text REFERENCES artworks ON DELETE CASCADE,
    edition_set_id  text,
    position        int,
    edition_of      text,
    sale_message    text,
    dimensions_in   text,
    dimensions_cm   text,
    is_acquireable  boolean,
    is_offerable    boolean,
    PRIMARY KEY (artwork_slug, edition_set_id)
);
