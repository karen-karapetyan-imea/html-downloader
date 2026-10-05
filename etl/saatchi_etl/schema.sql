-- Saatchi Art crawl: normalized schema.
-- Source: the Next.js page state (__NEXT_DATA__) embedded in each HTML page.
-- Money is stored in USD (source values are cents, divided by 100).
-- Safe to run repeatedly (IF NOT EXISTS everywhere).

-- One row per HTML file ------------------------------------------------------
CREATE TABLE IF NOT EXISTS crawl_pages (
    filename        text PRIMARY KEY,
    crawl_folder    text,
    url             text,            -- from crawl log, else canonical
    canonical_url   text,
    page_type       text,            -- artist_profile | artwork | unknown
    artist_id       bigint,
    artwork_id      bigint,
    status_code     int,
    error           text,
    block_detected  boolean,
    block_reason    text,
    duration_ms     int,
    crawled_at      timestamptz,     -- crawl log timestamp, else server render time
    parse_status    text,            -- ok | partial | skipped | error
    parse_error     text,
    loaded_at       timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crawl_pages_folder_idx ON crawl_pages(crawl_folder);
CREATE INDEX IF NOT EXISTS crawl_pages_type_idx ON crawl_pages(page_type);

-- Raw page state, so any field not modelled below can still be queried later.
CREATE TABLE IF NOT EXISTS raw_pages (
    filename    text PRIMARY KEY REFERENCES crawl_pages ON DELETE CASCADE,
    page_type   text,
    state       jsonb            -- {initialProps, page}; global site config stripped
);

-- Lookups ------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tags (
    tag_id  serial PRIMARY KEY,
    kind    text NOT NULL,       -- style | medium | material | keyword
    name    text NOT NULL,
    UNIQUE (kind, name)
);

CREATE TABLE IF NOT EXISTS badges (
    badge_id     serial PRIMARY KEY,
    title        text UNIQUE NOT NULL,
    description  text,
    image_url    text
);

-- Artists --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artists (
    artist_id              bigint PRIMARY KEY,
    username               text,
    first_name             text,
    last_name              text,
    full_name              text,
    profile_url            text,
    account_type           text,
    avatar_url             text,
    avatar_large_url       text,
    hero_image_url         text,
    hero_image_small_url   text,
    studio_image_url       text,
    youtube_id             text,
    city                   text,
    state                  text,
    zipcode                text,
    country                text,
    country_code           text,
    representation_city    text,
    representation_state   text,
    representation_country text,
    joined_at              timestamptz,
    followers_count        int,
    artworks_total         int,
    can_index              boolean,
    is_on_vacation         boolean,
    is_banned              boolean,
    reasons_not_allowed_to_sell text[],
    about                  text,
    education              text,
    events                 text,
    exhibitions            text,
    description            text,
    website_url            text,
    facebook_url           text,
    instagram_url          text,
    pinterest_url          text,
    tiktok_url             text,
    twitter_url            text,
    tumblr_url             text,
    meta_title             text,
    meta_description       text,
    profile_file           text,     -- profile page this came from (null if only seen via artworks)
    crawled_at             timestamptz,
    updated_at             timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS artists_country_idx ON artists(country_code);
CREATE INDEX IF NOT EXISTS artists_username_idx ON artists(lower(username));

CREATE TABLE IF NOT EXISTS artist_badges (
    artist_id  bigint REFERENCES artists ON DELETE CASCADE,
    badge_id   int REFERENCES badges,
    position   int,
    PRIMARY KEY (artist_id, badge_id)
);
CREATE INDEX IF NOT EXISTS artist_badges_badge_idx ON artist_badges(badge_id);

CREATE TABLE IF NOT EXISTS artist_collections (
    artist_id       bigint REFERENCES artists ON DELETE CASCADE,
    is_featured     boolean,
    position        int,
    title           text,
    collection_url  text,
    image_url       text,
    artworks_count  int,
    PRIMARY KEY (artist_id, is_featured, position)
);

CREATE TABLE IF NOT EXISTS artist_studio_images (
    artist_id    bigint REFERENCES artists ON DELETE CASCADE,
    position     int,
    image_url    text,
    description  text,
    PRIMARY KEY (artist_id, position)
);

-- Artworks -------------------------------------------------------------------------
-- Filled from artist profile listings (summary fields) and artwork detail pages
-- (full fields; has_detail = true). Values from both are merged.
CREATE TABLE IF NOT EXISTS artworks (
    artwork_id              bigint PRIMARY KEY,
    legacy_id               bigint,          -- id used in the public URL
    artist_id               bigint REFERENCES artists ON DELETE CASCADE,
    title                   text,
    slug                    text,
    url                     text,
    print_url               text,
    category                text,
    subject                 text,
    description             text,
    year_produced           int,
    width_cm                numeric,
    height_cm               numeric,
    depth_cm                numeric,
    size_bin                text,            -- small | medium | large | oversized
    panels                  int,
    is_multi_panel          boolean,
    aspect_ratio            numeric,
    original_status         text,            -- avail | sold | reserved | unavail
    has_original            boolean,
    has_open_editions       boolean,
    has_limited_editions    boolean,
    has_prints              boolean,
    sku                     text,            -- original product SKU
    legacy_sku              text,
    price_usd               numeric(12,2),   -- original, US list price
    freight_usd             numeric(12,2),
    min_print_price_usd     numeric(12,2),
    print_sizes             text[],          -- from profile listing
    print_materials         text[],
    is_available_for_sale   boolean,
    is_sold_out             boolean,
    is_reserved             boolean,
    is_final_sale           boolean,
    is_framed               boolean,
    frame_color             text,
    is_ready_to_hang        boolean,
    packaging_option        text,
    ships_from_country      text,
    ship_width              numeric,         -- shipping box, units as given by the site
    ship_height             numeric,
    ship_depth              numeric,
    ship_weight             numeric,
    views                   int,
    likes                   int,
    visibility              text,
    curation_status         int,
    curator_name            text,
    curator_title           text,
    is_find_similar_available boolean,
    uploaded_at             timestamptz,
    image_url               text,
    image_base_url          text,
    image_fullscreen_url    text,
    image_thumbnail_url     text,
    print_image_url         text,
    image_width             int,
    image_height            int,
    meta_title              text,
    meta_description        text,
    return_days             int,
    has_detail              boolean,
    detail_file             text,
    crawled_at              timestamptz,
    updated_at              timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS artworks_artist_idx ON artworks(artist_id);
CREATE INDEX IF NOT EXISTS artworks_category_idx ON artworks(category);
CREATE INDEX IF NOT EXISTS artworks_status_idx ON artworks(original_status);
CREATE INDEX IF NOT EXISTS artworks_price_idx ON artworks(price_usd);
CREATE INDEX IF NOT EXISTS artworks_legacy_idx ON artworks(legacy_id);

CREATE TABLE IF NOT EXISTS artwork_tags (
    artwork_id  bigint REFERENCES artworks ON DELETE CASCADE,
    tag_id      int REFERENCES tags,
    position    int,
    PRIMARY KEY (artwork_id, tag_id)
);
CREATE INDEX IF NOT EXISTS artwork_tags_tag_idx ON artwork_tags(tag_id);

CREATE TABLE IF NOT EXISTS artwork_images (
    artwork_id      bigint REFERENCES artworks ON DELETE CASCADE,
    position        int,             -- 0 = main image
    kind            text,            -- main | additional
    image_url       text,
    base_url        text,
    fullscreen_url  text,
    thumbnail_url   text,
    width           int,
    height          int,
    aspect_ratio    numeric,
    caption         text,
    PRIMARY KEY (artwork_id, position)
);

-- Sellable products of an artwork: the original and print editions
CREATE TABLE IF NOT EXISTS products (
    sku                    text PRIMARY KEY,
    artwork_id             bigint REFERENCES artworks ON DELETE CASCADE,
    product_type_id        int,
    product_type           text,     -- original | open_edition | limited_edition
    legacy_sku             text,
    material               text,
    width                  numeric,
    height                 numeric,
    depth                  numeric,
    units_produced         int,
    is_available_for_sale  boolean,
    is_sold_out            boolean,
    is_reserved            boolean,
    is_final_sale          boolean,
    is_aple                boolean,
    price_usd              numeric(12,2),
    freight_usd            numeric(12,2),
    shipping_domestic_usd  numeric(12,2),
    shipping_intl_usd      numeric(12,2),
    recommended_option_id  text
);
CREATE INDEX IF NOT EXISTS products_artwork_idx ON products(artwork_id);

-- Framing / size options for print products
CREATE TABLE IF NOT EXISTS product_options (
    sku                   text REFERENCES products ON DELETE CASCADE,
    option_id             text,
    position              int,
    title                 text,
    description           text,
    extended_description  text,
    framing_type_id       int,
    price_usd             numeric(12,2),
    width                 numeric,
    height                numeric,
    PRIMARY KEY (sku, option_id)
);

-- Original-artwork price and freight per shipping region (US, EU1C, AP1F, ...)
CREATE TABLE IF NOT EXISTS artwork_region_prices (
    artwork_id    bigint REFERENCES artworks ON DELETE CASCADE,
    region_code   text,
    price_usd     numeric(12,2),
    freight_usd   numeric(12,2),
    PRIMARY KEY (artwork_id, region_code)
);

-- Currency rates the site used on the crawl date
CREATE TABLE IF NOT EXISTS exchange_rates (
    as_of_date     date,
    currency_code  text,
    currency_name  text,
    rate_per_usd   numeric,
    PRIMARY KEY (as_of_date, currency_code)
);
