-- Artmajeur crawl: normalized schema.
-- Sources per page: JSON-LD (Product / ProfilePage / Review), the analytics dataLayer object,
-- and the server-rendered HTML. Prices are the USD amounts the site displayed.
-- Artists' street addresses are published in the JSON-LD but deliberately not stored.
-- Safe to run repeatedly.

CREATE TABLE IF NOT EXISTS crawl_pages (
    filename        text PRIMARY KEY,
    crawl_folder    text,
    url             text,
    canonical_url   text,
    page_type       text,            -- artwork | artist | unknown
    artist_id       bigint,
    artwork_id      bigint,
    status_code     int,
    error           text,
    block_detected  boolean,
    block_reason    text,
    duration_ms     int,
    crawled_at      timestamptz,
    parse_status    text,            -- ok | skipped | error
    parse_error     text,
    loaded_at       timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crawl_pages_folder_idx ON crawl_pages(crawl_folder);

-- Structured data exactly as found (JSON-LD blocks + dataLayer), for fields not modelled below.
CREATE TABLE IF NOT EXISTS raw_pages (
    filename   text PRIMARY KEY REFERENCES crawl_pages ON DELETE CASCADE,
    page_type  text,
    state      jsonb
);

-- Artists --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artists (
    artist_id               bigint PRIMARY KEY,   -- Artmajeur account id
    slug                    text,
    name                    text,
    url                     text,
    job_title               text,                 -- e.g. "Painter,Illustrator"
    categories              text,                 -- e.g. "painting, printmaking"
    gender                  text,
    biography               text,
    city                    text,
    region                  text,
    postal_code             text,
    country_code            text,
    nationality             text,
    birth_date              text,                 -- as shown ("1995", "unknown date", ...)
    birth_year              int,
    artistic_domains        text[],
    social_links            text[],
    portrait_url            text,
    rating_value            numeric,
    rating_count            int,
    followers_count         int,
    followed_artists_count  int,
    favorited_artworks_count int,                 -- times their works were added to favorites
    favorite_artworks_count int,                  -- size of their own favorites selection
    image_views             bigint,
    reviews_count           int,
    member_since            int,
    last_modified_at        date,
    profile_created_at      timestamptz,
    profile_modified_at     timestamptz,
    certified_value         numeric,              -- "Artist value certified" (Akoun)
    certified_value_currency text,
    certified_value_usd     numeric,
    certified_value_category text,
    certified_value_year    int,
    certified_on            date,
    certified_by            text,
    meta_title              text,
    meta_description        text,
    has_page                boolean,              -- the artist's own profile page was crawled
    source_file             text,
    crawled_at              timestamptz,
    updated_at              timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS artists_country_idx ON artists(country_code);
CREATE INDEX IF NOT EXISTS artists_slug_idx ON artists(slug);

CREATE TABLE IF NOT EXISTS artist_recognitions (      -- Award Winning, Editor's Pick, ...
    artist_id    bigint REFERENCES artists ON DELETE CASCADE,
    position     int,
    name         text,
    description  text,
    PRIMARY KEY (artist_id, position)
);

CREATE TABLE IF NOT EXISTS artist_groups (            -- groups and representing galleries
    artist_id  bigint REFERENCES artists ON DELETE CASCADE,
    position   int,
    kind       text,                                  -- group | gallery
    name       text,
    details    text,
    url        text,
    PRIMARY KEY (artist_id, position)
);

CREATE TABLE IF NOT EXISTS artist_influences (
    artist_id  bigint REFERENCES artists ON DELETE CASCADE,
    position   int,
    name       text,
    PRIMARY KEY (artist_id, position)
);

CREATE TABLE IF NOT EXISTS artist_education (
    artist_id     bigint REFERENCES artists ON DELETE CASCADE,
    education_id  bigint,
    years         text,
    start_year    int,
    end_year      int,
    description   text,
    PRIMARY KEY (artist_id, education_id)
);

CREATE TABLE IF NOT EXISTS artist_achievements (      -- awards, solo/group shows, press, residencies, collections
    artist_id       bigint REFERENCES artists ON DELETE CASCADE,
    achievement_id  bigint,
    kind            text,
    year            int,
    description     text,
    file_url        text,
    PRIMARY KEY (artist_id, achievement_id)
);

CREATE TABLE IF NOT EXISTS artist_events (            -- ongoing and upcoming art events
    artist_id    bigint REFERENCES artists ON DELETE CASCADE,
    position     int,
    title        text,
    details      text,
    url          text,
    PRIMARY KEY (artist_id, position)
);

CREATE TABLE IF NOT EXISTS artist_news (
    artist_id     bigint REFERENCES artists ON DELETE CASCADE,
    position      int,
    news_id       bigint,
    title         text,
    added_on      date,
    url           text,
    external_url  text,
    body          text,
    PRIMARY KEY (artist_id, position)
);

CREATE TABLE IF NOT EXISTS artist_reviews (
    artist_id       bigint REFERENCES artists ON DELETE CASCADE,
    position        int,
    rating          numeric,
    body            text,
    published_on    date,
    author_name     text,
    author_url      text,
    author_country  text,
    PRIMARY KEY (artist_id, position)
);

-- Artworks -------------------------------------------------------------------------
-- Full rows come from artwork pages (has_detail = true); summary rows come from the artwork
-- cards on profile pages and "See more from" blocks. Both are merged into one row.
CREATE TABLE IF NOT EXISTS artworks (
    artwork_id             bigint PRIMARY KEY,
    artist_id              bigint REFERENCES artists ON DELETE SET NULL,
    title                  text,
    slug                   text,
    url                    text,
    year                   int,
    category               text,             -- painting, photography, ...
    medium                 text,             -- display, e.g. "Painting"
    technique              text,             -- e.g. "Acrylic"
    support                text,             -- e.g. "Canvas"
    sub_categories         text,
    style                  text,
    theme                  text,
    edition_type           text,             -- Original Artwork (One Of A Kind) / Limited Edition (#3/10) / ...
    edition_size           int,              -- total copies of a limited edition
    copies_available       text,
    dimensions             text,
    height_in              numeric,
    width_in               numeric,
    depth_in               numeric,
    height_cm              numeric,
    width_cm               numeric,
    depth_cm               numeric,
    condition              text,
    framing                text,
    is_framed              boolean,
    fit_for_outdoor        boolean,
    is_ai_generated        boolean,
    is_one_of_a_kind       boolean,
    is_signed              boolean,
    signature_details      text,
    has_certificate        boolean,
    is_ready_to_hang       boolean,
    mounted_on             text,
    favorites_count        int,              -- "appears in N collections"
    availability           text,             -- for_sale | sold | not_for_sale
    sale_type              text,             -- tab label: Original / Limited Edition
    price_usd              numeric(12,2),    -- displayed price
    offer_price_usd        numeric(12,2),    -- JSON-LD offer price
    shipping_included      boolean,
    ships_from_country     text,
    packaging              text,
    ships_within_days      int,
    return_days            int,
    last_copies            boolean,
    has_prints             boolean,
    print_price_from_usd   numeric(12,2),
    print_types            text[],
    print_sizes            text[],
    print_framings         text[],
    print_finishes         text[],
    has_licences           boolean,
    licence_price_from_usd numeric(12,2),
    licence_types          text[],
    description            text,
    keywords               text[],
    badges                 text[],
    image_url              text,
    image_hd_url           text,
    published_on           date,
    meta_description       text,
    details                jsonb,            -- every "label: value" line of the details list
    has_detail             boolean,
    source_file            text,
    crawled_at             timestamptz,
    updated_at             timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS artworks_artist_idx ON artworks(artist_id);
CREATE INDEX IF NOT EXISTS artworks_category_idx ON artworks(category);
CREATE INDEX IF NOT EXISTS artworks_availability_idx ON artworks(availability);
CREATE INDEX IF NOT EXISTS artworks_price_idx ON artworks(price_usd);
CREATE INDEX IF NOT EXISTS artworks_keywords_idx ON artworks USING gin(keywords);

CREATE TABLE IF NOT EXISTS artwork_categories (         -- category chips, e.g. "Abstract", "Paintings under $500"
    artwork_id  bigint REFERENCES artworks ON DELETE CASCADE,
    position    int,
    name        text,
    url         text,
    PRIMARY KEY (artwork_id, position)
);
