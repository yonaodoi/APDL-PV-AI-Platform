BEGIN;

CREATE SCHEMA IF NOT EXISTS pv;

CREATE TABLE IF NOT EXISTS pv.roles (
    role_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    role_name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pv.permissions (
    permission_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    permission_code VARCHAR(100) NOT NULL UNIQUE,
    permission_name VARCHAR(150) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pv.role_permissions (
    role_id BIGINT NOT NULL
        REFERENCES pv.roles(role_id)
        ON DELETE CASCADE,

    permission_id BIGINT NOT NULL
        REFERENCES pv.permissions(permission_id)
        ON DELETE CASCADE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE IF NOT EXISTS pv.users (
    user_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    role_id BIGINT NOT NULL
        REFERENCES pv.roles(role_id),

    username VARCHAR(100) NOT NULL,
    full_name VARCHAR(200) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash TEXT NOT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    failed_login_attempts INTEGER NOT NULL DEFAULT 0
        CHECK (failed_login_attempts >= 0),

    locked_until TIMESTAMPTZ,
    last_login_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower
    ON pv.users (LOWER(username));

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower
    ON pv.users (LOWER(email));

CREATE TABLE IF NOT EXISTS pv.countries (
    country_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    country_name VARCHAR(150) NOT NULL UNIQUE,
    iso_alpha2 CHAR(2) UNIQUE,
    iso_alpha3 CHAR(3) UNIQUE,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pv.regulatory_authorities (
    authority_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    country_id BIGINT NOT NULL
        REFERENCES pv.countries(country_id),

    authority_name VARCHAR(255) NOT NULL,
    authority_code VARCHAR(50) NOT NULL,
    reporting_portal TEXT,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (country_id, authority_code)
);

INSERT INTO pv.roles (
    role_name,
    description
)
VALUES
    (
        'System Administrator',
        'Manages system configuration, users and access'
    ),
    (
        'QPPV',
        'Provides pharmacovigilance oversight'
    ),
    (
        'Deputy QPPV',
        'Supports the QPPV and performs delegated PV duties'
    ),
    (
        'PV Officer',
        'Performs pharmacovigilance activities'
    ),
    (
        'Case Processor',
        'Captures and processes safety cases'
    ),
    (
        'Medical Reviewer',
        'Performs medical review of safety information'
    ),
    (
        'Quality Reviewer',
        'Performs quality review of PV records'
    ),
    (
        'Regulatory Affairs Officer',
        'Manages regulatory reporting activities'
    ),
    (
        'Safety Issue Coordinator',
        'Coordinates safety issues and escalations'
    ),
    (
        'PV-Labelling Team',
        'Supports safety labelling activities'
    ),
    (
        'Auditor',
        'Reviews records and audit trails'
    ),
    (
        'Read-only User',
        'Can view authorised information without editing'
    )
ON CONFLICT (role_name) DO NOTHING;

INSERT INTO pv.permissions (
    permission_code,
    permission_name,
    description
)
VALUES
    (
        'case.view',
        'View safety cases',
        'Allows the user to view authorised safety cases'
    ),
    (
        'case.create',
        'Create safety cases',
        'Allows the user to create safety cases'
    ),
    (
        'case.edit',
        'Edit safety cases',
        'Allows the user to update safety-case information'
    ),
    (
        'case.review',
        'Review safety cases',
        'Allows the user to perform case review'
    ),
    (
        'report.generate',
        'Generate reports',
        'Allows the user to generate controlled reports'
    ),
    (
        'psur.manage',
        'Manage PSURs',
        'Allows the user to prepare and manage PSURs'
    ),
    (
        'signal.manage',
        'Manage signals',
        'Allows the user to manage safety signals'
    ),
    (
        'regulatory.manage',
        'Manage regulatory reporting',
        'Allows the user to manage regulatory submissions'
    ),
    (
        'audit.view',
        'View audit trail',
        'Allows the user to review audit records'
    ),
    (
        'administration.manage',
        'Manage administration',
        'Allows the user to manage system configuration'
    )
ON CONFLICT (permission_code) DO NOTHING;

INSERT INTO pv.countries (
    country_name,
    iso_alpha2,
    iso_alpha3
)
VALUES
    ('Uganda', 'UG', 'UGA'),
    ('Kenya', 'KE', 'KEN'),
    ('Tanzania', 'TZ', 'TZA'),
    ('Rwanda', 'RW', 'RWA'),
    ('Burundi', 'BI', 'BDI'),
    ('South Sudan', 'SS', 'SSD'),
    ('Malawi', 'MW', 'MWI'),
    ('Botswana', 'BW', 'BWA'),
    ('Zambia', 'ZM', 'ZMB'),
    ('Ethiopia', 'ET', 'ETH'),
    ('Mali', 'ML', 'MLI'),
    ('Cameroon', 'CM', 'CMR'),
    ('Côte d''Ivoire', 'CI', 'CIV'),
    ('Senegal', 'SN', 'SEN')
ON CONFLICT (country_name) DO NOTHING;

COMMIT;