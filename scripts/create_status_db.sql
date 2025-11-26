-- ============================================================================
-- System Status Database Creation Script
-- Creates the system_status database for HPC resource monitoring
-- ============================================================================

-- Create database
CREATE DATABASE IF NOT EXISTS system_status
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE system_status;

-- Grant privileges (adjust username as needed)
-- GRANT ALL PRIVILEGES ON system_status.* TO 'status_user'@'localhost';
-- FLUSH PRIVILEGES;

-- Note: Tables will be created by the Python ORM setup script
-- This SQL script only creates the database itself
