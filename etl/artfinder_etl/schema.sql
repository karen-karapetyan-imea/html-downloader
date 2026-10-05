-- Artfinder crawl: normalized schema.
-- Source: the Next.js page state (__NEXT_DATA__) in each HTML page.
-- Prices are stored in all five currencies the site shows (GBP is the site's base currency).
-- Artists' exact coordinates are published by the site; only a rounded (~10 km) location is stored.
-- Safe to run repeatedly.

CREATE TABLE IF NOT EXISTS crawl_pages (
    filename        text PRIMARY KEY,
    crawl_folder    text,
    url             text,
    canonical_url   text,
    page_type       text,            -- artwork | artist | listing | unknown
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

CREATE TABLE IF NOT EXISTS raw_pages (
    filename   text PRIMARY KEY REFERENCES crawl_pages ON DELETE CASCADE,
    page_type  text,
    state      jsonb            -- page props without site-wide config
);

CREATE TABLE IF NOT EXISTS currency_rates (
    as_of_date  date,
    base        text,
    quote       text,
    rate        numeric,
    PRIMARY KEY (as_of_date, base, quote)
);

-- Artists --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artists (
    artist_id                 bigint PRIMARY KEY,
    username                  text,
    slug                      text,
    name                      text,
    url                       text,
    provider_id               bigint,          -- seller account
    provider_slug             text,
    provider_display_name     text,
    country                   text,
    country_code              text,
    approx_lat                numeric(5,1),    -- rounded to ~10 km
    approx_lng                numeric(5,1),
    intro                     text,            -- plain text
    intro_html                text,
    biography                 text,
    avatar_url                text,
    cover_url                 text,
    joined_at                 timestamptz,
    followers_count           int,             -- "Follow (N)"
    artworks_for_sale         int,
    artworks_total            int,             -- artworks listed in the shop
    count_on_sale             int,
    count_ready_to_hang       int,
    count_in_stock            int,
    count_exclusive           int,             -- exclusive to Artfinder
    accepts_commissions       boolean,
    has_active_subscription   boolean,
    is_represented_by_gallery boolean,
    has_me_at_work            boolean,
    in_sale                   boolean,
    sale_discount_amount      numeric,
    website_url               text,
    facebook_url              text,
    instagram_username        text,
    pinterest_username        text,
    twitter_username          text,
    review_count              int,
    seller_rating             numeric,
    listing_rating            numeric,
    communication_rating      numeric,
    delivery_rating           numeric,
    reviews_5_star            int,
    reviews_4_star            int,
    reviews_3_star            int,
    reviews_2_star            int,
    reviews_1_star            int,
    has_page                  boolean,         -- the artist's own page was crawled
    source_file               text,
    crawled_at                timestamptz,
    updated_at                timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS artists_country_idx ON artists(country_code);
CREATE INDEX IF NOT EXISTS artists_slug_idx ON artists(slug);

CREATE TABLE IF NOT EXISTS artist_social_links (
    artist_id  bigint REFERENCES artists ON DELETE CASCADE,
    position   int,
    platform   text,
    url        text,
    PRIMARY KEY (artist_id, position)
);

CREATE TABLE IF NOT EXISTS artist_awards (
    artist_id    bigint REFERENCES artists ON DELETE CASCADE,
    position     int,
    year         text,
    title        text,
    description  text,
    PRIMARY KEY (artist_id, position)
);

CREATE TABLE IF NOT EXISTS artist_education (
    artist_id  bigint REFERENCES artists ON DELETE CASCADE,
    position   int,
    school     text,
    years      text,
    PRIMARY KEY (artist_id, position)
);

CREATE TABLE IF NOT EXISTS artist_events (
    artist_id    bigint REFERENCES artists ON DELETE CASCADE,
    kind         text,            -- upcoming | previous
    position     int,
    title        text,
    venue        text,
    dates        text,
    description  text,
    PRIMARY KEY (artist_id, kind, position)
);

CREATE TABLE IF NOT EXISTS artist_featured_in (     -- press, blog posts, collections featuring the artist
    artist_id     bigint REFERENCES artists ON DELETE CASCADE,
    position      int,
    type          text,
    label         text,
    title         text,
    description   text,
    preview_text  text,
    link          text,
    image_path    text,
    PRIMARY KEY (artist_id, position)
);

CREATE TABLE IF NOT EXISTS artist_collections (     -- shop sections on the artist page
    artist_id      bigint REFERENCES artists ON DELETE CASCADE,
    position       int,
    collection_id  text,
    name           text,
    PRIMARY KEY (artist_id, position)
);

-- Artworks -------------------------------------------------------------------------
-- Full rows come from artwork pages (has_detail = true); summary rows from artwork cards on
-- artist and listing pages. Both merge into one row per artwork.
CREATE TABLE IF NOT EXISTS artworks (
    artwork_id                 bigint PRIMARY KEY,
    artist_id                  bigint REFERENCES artists ON DELETE SET NULL,
    name                       text,
    slug                       text,
    url                        text,
    category_slug              text,
    category_name              text,
    style_slug                 text,
    style_name                 text,
    subject_slug               text,
    subject_name               text,
    description                text,
    year_made                  int,
    is_unique                  boolean,          -- one of a kind
    edition_size               int,              -- limited edition of N
    quantity                   int,
    is_in_stock                boolean,
    is_new                     boolean,
    is_framed                  boolean,
    framed_text                text,
    is_ready_to_hang           boolean,
    substrate                  text,
    materials                  text,
    signature                  text,
    tags                       text[],
    dimensions_cm              text,
    dimensions_in              text,
    width_cm                   numeric,
    height_cm                  numeric,
    depth_cm                   numeric,
    dimensions_type            text,             -- framed | unframed
    shipping_width             numeric,
    shipping_height            numeric,
    shipping_depth             numeric,
    shipping_weight            numeric,
    original_currency          text,
    original_amount            numeric(12,2),
    price_gbp                  numeric(12,2),
    price_usd                  numeric(12,2),
    price_eur                  numeric(12,2),
    price_cad                  numeric(12,2),
    price_aud                  numeric(12,2),
    pre_sale_price_usd         numeric(12,2),    -- price before a sale discount
    pre_sale_price_gbp         numeric(12,2),
    in_sale                    boolean,
    discount_amount            numeric,
    sale_ends_at               timestamptz,
    accepts_offers             boolean,
    artist_accepts_commissions boolean,
    interest_count             int,              -- people who saved / showed interest
    review_count               int,              -- artist's review count shown on the page
    seller_rating              numeric,
    has_video                  boolean,
    video_link                 text,
    verified_advertising_safe  boolean,
    provider_managed_shipping  boolean,
    managed_shipping_table_id  bigint,
    holiday_message            text,
    average_colour             text,
    has_limited_edition_prints boolean,
    image_url                  text,
    image_width                int,
    image_height               int,
    has_detail                 boolean,
    source_file                text,
    crawled_at                 timestamptz,
    updated_at                 timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS artworks_artist_idx ON artworks(artist_id);
CREATE INDEX IF NOT EXISTS artworks_category_idx ON artworks(category_slug);
CREATE INDEX IF NOT EXISTS artworks_price_idx ON artworks(price_usd);
CREATE INDEX IF NOT EXISTS artworks_tags_idx ON artworks USING gin(tags);

CREATE TABLE IF NOT EXISTS artwork_images (
    artwork_id     bigint REFERENCES artworks ON DELETE CASCADE,
    position       int,
    image_type     text,
    url            text,
    retina_url     text,
    thumbnail_url  text,
    width          int,
    height         int,
    PRIMARY KEY (artwork_id, position)
);

CREATE TABLE IF NOT EXISTS artwork_categories (      -- category hierarchy, e.g. Painting > Oil painting
    artwork_id   bigint REFERENCES artworks ON DELETE CASCADE,
    position     int,
    slug         text,
    name         text,
    full_name    text,
    depth        int,
    parent_slug  text,
    PRIMARY KEY (artwork_id, position)
);

CREATE TABLE IF NOT EXISTS artwork_featured_collections (
    artwork_id   bigint REFERENCES artworks ON DELETE CASCADE,
    position     int,
    slug         text,
    title        text,
    url          text,
    description  text,
    PRIMARY KEY (artwork_id, position)
);

-- Limited-edition print variants listed on artwork cards
CREATE TABLE IF NOT EXISTS artwork_print_editions (
    artwork_id   bigint REFERENCES artworks ON DELETE CASCADE,
    edition_id   bigint,
    slug         text,
    edition_of   int,
    quantity     int,
    width        numeric,
    height       numeric,
    depth        numeric,
    units        text,
    price_gbp    numeric(12,2),
    price_usd    numeric(12,2),
    price_eur    numeric(12,2),
    PRIMARY KEY (artwork_id, edition_id)
);
