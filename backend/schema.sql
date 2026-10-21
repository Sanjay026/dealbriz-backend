CREATE DATABASE IF NOT EXISTS dealbridge;
USE dealbridge;

CREATE TABLE users (
  id            CHAR(36)      NOT NULL PRIMARY KEY,
  email         VARCHAR(150)  NOT NULL UNIQUE,
  password_hash VARCHAR(255)  NOT NULL,
  first_name    VARCHAR(100)  NOT NULL DEFAULT '',
  last_name     VARCHAR(100)  NOT NULL DEFAULT '',
  phone         VARCHAR(20),
  city          VARCHAR(10),
  avatar_url    TEXT,
  is_admin      BOOLEAN       NOT NULL DEFAULT FALSE,
  created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE products (
  id              CHAR(36)      NOT NULL PRIMARY KEY,
  title           VARCHAR(200)  NOT NULL,
  category        VARCHAR(30)   NOT NULL,
  `condition`     VARCHAR(20)   NOT NULL,
  price           DECIMAL(12,2) NOT NULL,
  original_price  DECIMAL(12,2),
  negotiable      BOOLEAN       NOT NULL DEFAULT FALSE,
  emi_eligible    BOOLEAN       NOT NULL DEFAULT TRUE,
  description     TEXT,
  image_url       TEXT,
  extra_images    TEXT,
  posted_by       CHAR(36),
  seller_name     VARCHAR(200),
  seller_avatar   TEXT,
  seller_phone    VARCHAR(20),
  show_phone      VARCHAR(20)   NOT NULL DEFAULT 'chat_only',
  location        VARCHAR(200),
  city            VARCHAR(10)   NOT NULL,
  landmark        VARCHAR(200),
  brand           VARCHAR(100),
  model           VARCHAR(100),
  views           INT           NOT NULL DEFAULT 0,
  status          VARCHAR(20)   NOT NULL DEFAULT 'active',
  created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (posted_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE saved_products (
  id          CHAR(36)  NOT NULL PRIMARY KEY,
  user_id     CHAR(36)  NOT NULL,
  product_id  CHAR(36)  NOT NULL,
  saved_at    DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (user_id, product_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE emi_applications (
  id                      CHAR(36)      NOT NULL PRIMARY KEY,
  user_id                 CHAR(36)      NOT NULL,
  product_id              CHAR(36),
  product_title_snapshot  VARCHAR(200)  NOT NULL,
  product_image_snapshot  TEXT,
  product_price_snapshot  DECIMAL(12,2) NOT NULL,
  down_payment            DECIMAL(12,2) NOT NULL DEFAULT 0,
  tenure_months           INT           NOT NULL,
  interest_rate           DECIMAL(5,2)  NOT NULL,
  loan_amount             DECIMAL(12,2) NOT NULL,
  monthly_emi             DECIMAL(12,2) NOT NULL,
  total_payable           DECIMAL(12,2) NOT NULL,
  applicant_name          VARCHAR(200)  NOT NULL,
  applicant_phone         VARCHAR(20)   NOT NULL,
  applicant_income        DECIMAL(12,2),
  status                  VARCHAR(20)   NOT NULL DEFAULT 'pending',
  applied_at              DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at              DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL
);

CREATE TABLE messages (
  id            CHAR(36)  NOT NULL PRIMARY KEY,
  product_id    CHAR(36)  NOT NULL,
  sender_id     CHAR(36)  NOT NULL,
  receiver_id   CHAR(36)  NOT NULL,
  body          TEXT      NOT NULL,
  is_read       BOOLEAN   NOT NULL DEFAULT FALSE,
  created_at    DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
  FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (receiver_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Make yourself admin after signing up:
-- UPDATE users SET is_admin = TRUE WHERE email = 'your@email.com';
