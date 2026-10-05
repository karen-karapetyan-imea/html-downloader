-- Artsper artist-page crawl: normalized schema.
-- Every table carries enough raw text next to the parsed values that nothing on the page is lost.

CREATE TABLE IF NOT EXISTS crawl_pages (
    filename        text PRIMARY KEY,
    crawl_folder    text,            -- crawl date folder the latest version came from
    url             text NOT NULL,
    status_code     int,
    error           text,
    block_detected  boolean,
    block_reason    text,
    duration_ms     int,
    crawled_at      timestamptz,
    page_type       text,            -- artist | artist_listing | unknown
    artist_id       int,
    parse_status    text,            -- ok | skipped | error
    parse_error     text,
    loaded_at       timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS artists (
    artist_id            int PRIMARY KEY,
    name                 text,
    slug                 text,
    url                  text,
    country_slug         text,
    nationality          text,          -- e.g. "Italy"
    nationality_label    text,          -- e.g. "Italian"
    nationality_url      text,
    birth_year           int,
    death_year           int,
    followers_count      int,
    quote                text,
    description          text,          -- plain-text bio from JSON-LD
    biography_text       text,          -- visible bio, paragraphs joined by \n\n
    biography_html       text,
    image_url            text,          -- JSON-LD image
    cover_image_url      text,          -- page header image
    artworks_available_count int,       -- "N works available" from meta description
    artworks_on_page     int,
    movements_generic_list boolean,     -- page showed the site-wide movement list, not artist-specific
    artwork_pages_total  int,           -- from paginator (1 when no paginator)
    page_title           text,
    meta_description     text,
    robots               text,
    og_title             text,
    og_description       text,
    og_image             text,
    og_locale            text,
    page_headline        text,          -- JSON-LD WebPage.headline
    same_as              jsonb,         -- JSON-LD Person.sameAs (artist socials)
    breadcrumb           jsonb,
    gtm_page_context     jsonb,         -- decoded data-gtm-analytics of #page-context
    jsonld               jsonb,         -- every JSON-LD block on the page, verbatim
    meta_tags            jsonb,         -- every <meta> tag, verbatim
    source_file          text REFERENCES crawl_pages(filename),
    crawled_at           timestamptz,
    updated_at           timestamptz DEFAULT now()
);

-- Badges in the header: "Emerging artists", "Masterpieces", "Bestsellers", ...
CREATE TABLE IF NOT EXISTS artist_selections (
    artist_id  int REFERENCES artists ON DELETE CASCADE,
    selection  text,
    position   int,
    PRIMARY KEY (artist_id, selection)
);

-- "About" block: Nationality / Categories / Artistic movements / Themes (+ any future block title)
CREATE TABLE IF NOT EXISTS artist_attributes (
    artist_id  int REFERENCES artists ON DELETE CASCADE,
    attr_type  text,      -- nationality | categories | artistic_movements | themes | ...
    value      text,
    url        text,
    position   int,
    PRIMARY KEY (artist_id, attr_type, position)
);

-- "Discover the movements linked to X" carousel
CREATE TABLE IF NOT EXISTS artist_movement_cards (
    artist_id  int REFERENCES artists ON DELETE CASCADE,
    position   int,
    name       text,
    url        text,
    image_url  text,
    PRIMARY KEY (artist_id, position)
);

-- Category filter chips above the catalog ("All artworks", "Painting", ...)
CREATE TABLE IF NOT EXISTS artist_catalog_filters (
    artist_id  int REFERENCES artists ON DELETE CASCADE,
    position   int,
    label      text,
    url        text,
    is_active  boolean,
    PRIMARY KEY (artist_id, position)
);

CREATE TABLE IF NOT EXISTS artist_studio_images (
    artist_id  int REFERENCES artists ON DELETE CASCADE,
    position   int,
    image_url  text,
    caption    text,
    PRIMARY KEY (artist_id, position)
);

-- "Discover similar artists"
CREATE TABLE IF NOT EXISTS artist_similar (
    artist_id            int REFERENCES artists ON DELETE CASCADE,
    position             int,
    similar_artist_id    int,
    name                 text,
    nationality          text,
    url                  text,
    followers_count      int,
    image_url            text,
    PRIMARY KEY (artist_id, position)
);

-- "Discover other selections of artworks" chips
CREATE TABLE IF NOT EXISTS artist_related_links (
    artist_id          int REFERENCES artists ON DELETE CASCADE,
    position           int,
    linked_artist_id   int,
    label              text,
    url                text,
    PRIMARY KEY (artist_id, position)
);

CREATE TABLE IF NOT EXISTS exhibitions (
    exhibition_id  int PRIMARY KEY,
    name           text,
    gallery_name   text,
    url            text,
    image_url      text,
    date_text      text,
    start_date     date,
    end_date       date
);

CREATE TABLE IF NOT EXISTS artist_exhibitions (
    artist_id      int REFERENCES artists ON DELETE CASCADE,
    exhibition_id  int REFERENCES exhibitions,
    position       int,
    PRIMARY KEY (artist_id, exhibition_id)
);

CREATE TABLE IF NOT EXISTS artworks (
    artwork_id          int PRIMARY KEY,
    artist_id           int REFERENCES artists ON DELETE CASCADE,
    position            int,          -- order on the artist page
    title               text,
    url                 text,
    category            text,         -- slug, e.g. painting
    medium_label        text,         -- display, e.g. Painting
    dimensions_cm       text,
    width_cm            numeric,
    height_cm           numeric,
    depth_cm            numeric,
    dimensions_in       text,
    width_in            numeric,
    height_in           numeric,
    depth_in            numeric,
    price_text          text,         -- as displayed ("$2,557", "Sold", "Price upon request")
    price_usd           numeric,      -- current displayed USD price
    price_usd_old       numeric,      -- struck-through price when discounted
    is_discounted       boolean,
    is_sold             boolean,
    is_price_on_request boolean,
    price_usd_data      numeric,      -- img data-price
    price_eur           numeric,      -- GTM price (EUR)
    value_eur           numeric,
    currency            text,
    price_range         text,
    quantity            int,
    vendor_id           int,
    gtm_label           text,
    staff_pick          boolean,
    staff_pick_label    text,
    image_url           text,
    image_url_2x        text,
    image_alt           text,
    aspect_ratio        numeric,
    is_featured         boolean,      -- present in JSON-LD "Selected artworks"
    featured_position   int,
    featured_image_url  text,
    gtm                 jsonb,        -- full decoded GTM payload
    updated_at          timestamptz DEFAULT now()
);

ALTER TABLE crawl_pages ADD COLUMN IF NOT EXISTS crawl_folder text;
CREATE INDEX IF NOT EXISTS crawl_pages_folder_idx ON crawl_pages(crawl_folder);
CREATE INDEX IF NOT EXISTS artworks_artist_idx ON artworks(artist_id);
CREATE INDEX IF NOT EXISTS artworks_vendor_idx ON artworks(vendor_id);
CREATE INDEX IF NOT EXISTS artworks_category_idx ON artworks(category);
CREATE INDEX IF NOT EXISTS artist_attributes_value_idx ON artist_attributes(attr_type, value);
