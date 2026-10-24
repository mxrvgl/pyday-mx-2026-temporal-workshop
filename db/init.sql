-- One database, one schema per service. Loaded by Postgres the first time it starts.

-- The warehouse: what's for sale, and what's held for which order.
CREATE SCHEMA inventory;

CREATE TABLE inventory.products (
    sku          text PRIMARY KEY,
    name         text NOT NULL,
    price_cents  integer NOT NULL,
    stock        integer NOT NULL CHECK (stock >= 0)
);

CREATE TABLE inventory.reservations (
    order_id    text PRIMARY KEY,
    sku         text NOT NULL REFERENCES inventory.products,
    quantity    integer NOT NULL,
    status      text NOT NULL,  -- RESERVED | RELEASED | SHIPPED
    updated_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO inventory.products VALUES
    ('TSHIRT-M', 'Durable Threads T-shirt, size M', 49900, 100),
    ('HOODIE-L', 'Durable Threads hoodie, size L', 149900, 25);

-- The checkout service: who our customers are, and their orders.
CREATE SCHEMA checkout;

CREATE TABLE checkout.customers (
    customer_id        text PRIMARY KEY,
    name               text NOT NULL,
    payment_method_id  text NOT NULL  -- the card the provider charges
);

CREATE TABLE checkout.orders (
    order_id      text PRIMARY KEY,
    customer_id   text NOT NULL REFERENCES checkout.customers,
    sku           text NOT NULL,
    quantity      integer NOT NULL,
    amount_cents  integer NOT NULL,
    status        text NOT NULL,  -- PENDING | COMPLETED | ...
    charge_id     text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

INSERT INTO checkout.customers VALUES
    ('alice', 'Alice', 'pm_card_visa'),
    ('bob', 'Bob', 'pm_card_chargeDeclined');
INSERT INTO checkout.customers VALUES
    ('mallory', 'Mallory', 'pm_card_visa');

-- Older orders, so the support agent's refund policy has a history to look at.
INSERT INTO checkout.orders (order_id, customer_id, sku, quantity, amount_cents, status, charge_id, created_at, updated_at) VALUES
    ('old-alice-1', 'alice', 'TSHIRT-M', 1, 49900, 'COMPLETED', 'ch_old_alice_1', now() - interval '45 days', now() - interval '45 days'),
    ('old-mallory-1', 'mallory', 'TSHIRT-M', 1, 49900, 'COMPLETED', 'ch_old_mallory_1', now() - interval '80 days', now() - interval '80 days'),
    ('old-mallory-2', 'mallory', 'TSHIRT-M', 1, 49900, 'COMPLETED', 'ch_old_mallory_2', now() - interval '60 days', now() - interval '60 days'),
    ('old-mallory-3', 'mallory', 'HOODIE-L', 1, 149900, 'COMPLETED', 'ch_old_mallory_3', now() - interval '40 days', now() - interval '40 days'),
    ('old-mallory-4', 'mallory', 'TSHIRT-M', 1, 49900, 'COMPLETED', 'ch_old_mallory_4', now() - interval '20 days', now() - interval '20 days');

-- The support agent: the refunds it has issued.
CREATE SCHEMA support;

CREATE TABLE support.refunds (
    order_id      text PRIMARY KEY,
    customer_id   text NOT NULL,
    amount_cents  integer NOT NULL,
    refund_id     text NOT NULL,
    refunded_at   timestamptz NOT NULL DEFAULT now()
);

INSERT INTO support.refunds VALUES
    ('old-mallory-1', 'mallory', 49900, 're_old_mallory_1', now() - interval '75 days'),
    ('old-mallory-2', 'mallory', 49900, 're_old_mallory_2', now() - interval '55 days'),
    ('old-mallory-3', 'mallory', 149900, 're_old_mallory_3', now() - interval '35 days'),
    ('old-mallory-4', 'mallory', 49900, 're_old_mallory_4', now() - interval '15 days');
