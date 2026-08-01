    CREATE TABLE stones (
        id INTEGER PRIMARY KEY,
        stock_number TEXT NOT NULL UNIQUE,

        status TEXT NOT NULL,
        hold_client_id INTEGER,

        shade TEXT,
        milky TEXT,
        eye_clean TEXT,
        bgm TEXT,
        black TEXT,
        open_inclusion TEXT,

        pair_number TEXT,
        pair_stock_number TEXT,
        pair_separable INTEGER CHECK(pair_separable IN (0,1)),

        picture_link TEXT,
        video_link TEXT,

        current_country TEXT,
        current_state TEXT,
        current_city TEXT
    );

    CREATE TABLE grading_reports (
        id INTEGER PRIMARY KEY,
        stone_id INTEGER NOT NULL,

        report_number TEXT UNIQUE,
        lab TEXT,
        laser_inscription TEXT,

        lab_comments TEXT,
        key_to_symbols TEXT,
        internal_comments TEXT,

        certificate_image_link TEXT,

        shape TEXT NOT NULL,
        weight REAL NOT NULL,
        color TEXT NOT NULL,
        clarity TEXT NOT NULL,
        cut TEXT,
        polish TEXT,
        symmetry TEXT,
        size TEXT NOT NULL,
        fluorescence_intensity TEXT,
        fluorescence_color TEXT,
        fancy_color TEXT,
        fancy_intensity TEXT,
        overtone TEXT,
        depth_percent REAL,
        table_percent REAL,
        girdle_tn TEXT,
        girdle_thick TEXT,
        girdle_percent REAL,
        girdle_condition TEXT,

        culet_size TEXT,
        culet_condition TEXT,
        
        crown_height REAL,
        crown_angle REAL,
        pavilion_depth REAL,
        pavilion_angle REAL,

        rapaport_price_per_carat REAL,
        rapaport_discount REAL,
        price_per_carat REAL,
        total_price REAL,


        measurements TEXT NOT NULL,    

        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN(0,1)),

        FOREIGN KEY (stone_id) REFERENCES stones(id) ON DELETE CASCADE
    );

    CREATE TABLE stone_price_history (
        id INTEGER PRIMARY KEY,
        grading_report_id INTEGER NOT NULL,
        price_per_carat REAL NOT NULL,
        changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (grading_report_id) REFERENCES grading_reports(id)
    );

    CREATE TABLE rapaport_prices (
        id INTEGER PRIMARY KEY,

        shape TEXT NOT NULL,
        clarity TEXT NOT NULL,
        color TEXT NOT NULL,

        min_weight REAL NOT NULL,
        max_weight REAL NOT NULL,

        price_per_carat REAL NOT NULL,
        price_date DATE NOT NULL
    );

    CREATE TABLE clients (
        id INTEGER PRIMARY KEY,
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        address TEXT NOT NULL,

        polygon_id INTEGER UNIQUE,
        jbt_id INTEGER UNIQUE,
        rapnet_id INTEGER UNIQUE,

        tax_id TEXT NOT NULL UNIQUE,
        sales_tax_id TEXT NOT NULL UNIQUE
    );


    CREATE TABLE client_contacts (
        id INTEGER PRIMARY KEY,
        client_id INTEGER NOT NULL,

        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        email TEXT NOT NULL,
        fax TEXT,
        cell TEXT,

        FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
    );

    CREATE TABLE shipping_addresses (
        id INTEGER PRIMARY KEY,
        client_id INTEGER NOT NULL,

        label TEXT,
        manager TEXT,
        store_number TEXT,

        address TEXT NOT NULL,
        city TEXT,
        state TEXT,
        country TEXT,
        phone TEXT,

        FOREIGN KEY (client_id) REFERENCES clients(id)
    );

    CREATE TABLE transactions (
        id INTEGER PRIMARY KEY,
        transaction_number TEXT NOT NULL UNIQUE,

        client_id INTEGER NOT NULL,

        type TEXT NOT NULL
            CHECK (type IN (
                'memo',
                'invoice',
                'credit_invoice'
            )),

        status TEXT NOT NULL
            CHECK (
                status IN (
                    'draft',
                    'active',
                    'return',
                    'completed',
                    'cancelled'
                )
            ),

        parent_transaction_id INTEGER,

        person TEXT NOT NULL,
        phone TEXT,
        fax TEXT,

        date DATE NOT NULL,
        terms TEXT NOT NULL,

        carrier TEXT NOT NULL,
        shipment_type TEXT NOT NULL,
        ship_charge REAL NOT NULL,

        ship_to_address_id INTEGER,
        source_shipping_address_id INTEGER,
        ship_to_label TEXT,
        ship_to_manager TEXT,
        ship_to_store_number TEXT,
        ship_to_address_snapshot TEXT,
        ship_to_city TEXT,
        ship_to_state TEXT,
        ship_to_country TEXT,
        ship_to_phone TEXT,

        source_contact_id INTEGER,
        contact_email_snapshot TEXT,
        contact_cell_snapshot TEXT,
        purchase_order_number TEXT,

        FOREIGN KEY (client_id)
            REFERENCES clients(id),

        FOREIGN KEY (ship_to_address_id)
            REFERENCES shipping_addresses(id),

        FOREIGN KEY (source_shipping_address_id)
            REFERENCES shipping_addresses(id),

        FOREIGN KEY (source_contact_id)
            REFERENCES client_contacts(id),

        FOREIGN KEY (parent_transaction_id)
            REFERENCES transactions(id)
    );


    CREATE TABLE transaction_items (
        id INTEGER PRIMARY KEY,

        transaction_id INTEGER NOT NULL,

        stone_id INTEGER NOT NULL,
        grading_report_id INTEGER NOT NULL,

        created_from_item_id INTEGER,

        status TEXT NOT NULL
            CHECK (
                status IN (
                    'draft',
                    'active',
                    'return',
                    'returned',
                    'credited',
                    'invoiced'
                )
            ),

        stock_number TEXT NOT NULL,
        report_number TEXT,

        lab TEXT,

        shape TEXT NOT NULL,
        weight REAL NOT NULL,
        color TEXT NOT NULL,
        clarity TEXT NOT NULL,

        cut TEXT,
        polish TEXT,
        symmetry TEXT,

        fluorescence_intensity TEXT,

        price_per_carat REAL NOT NULL,
        total_price REAL NOT NULL,

        FOREIGN KEY (transaction_id)
            REFERENCES transactions(id)
            ON DELETE CASCADE,

        FOREIGN KEY (stone_id)
            REFERENCES stones(id),

        FOREIGN KEY (grading_report_id)
            REFERENCES grading_reports(id),

        FOREIGN KEY (created_from_item_id)
            REFERENCES transaction_items(id),

        UNIQUE (transaction_id, stone_id)
    );

    CREATE TABLE stock_number_sequence (
        shape TEXT PRIMARY KEY,
        last_number INTEGER NOT NULL
    );

    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('ADMIN', 'MANAGER', 'SALES', 'ACCOUNTING')),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE transaction_number_counters (
        transaction_type TEXT NOT NULL CHECK(transaction_type IN ('memo', 'invoice', 'credit_invoice')),
        transaction_date DATE NOT NULL,
        last_value INTEGER NOT NULL CHECK(last_value >= 0),
        PRIMARY KEY (transaction_type, transaction_date)
    );

    CREATE TABLE receiving_events (
        id INTEGER PRIMARY KEY,
        stone_id INTEGER NOT NULL,
        stock_number_snapshot TEXT NOT NULL,
        source_transaction_id INTEGER,
        source_transaction_item_id INTEGER,
        received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        received_by_user_id INTEGER NOT NULL,
        note TEXT,

        FOREIGN KEY (stone_id) REFERENCES stones(id),
        FOREIGN KEY (source_transaction_id) REFERENCES transactions(id),
        FOREIGN KEY (source_transaction_item_id) REFERENCES transaction_items(id),
        FOREIGN KEY (received_by_user_id) REFERENCES users(id)
    );

    CREATE TABLE schema_migrations (
        name TEXT PRIMARY KEY,
        applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX idx_clients_code ON clients(code);

    CREATE INDEX idx_grading_reports_stone ON grading_reports(stone_id);

    CREATE INDEX idx_transaction_items_tx ON transaction_items(transaction_id);

    CREATE INDEX idx_transaction_items_stone ON transaction_items(stone_id);

    CREATE INDEX idx_receiving_events_stone ON receiving_events(stone_id);
    CREATE INDEX idx_receiving_events_received_at ON receiving_events(received_at);
