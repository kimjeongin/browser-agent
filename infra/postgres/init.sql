-- keycloak DB 생성
CREATE DATABASE keycloak;

-- browser_agent DB에 pgvector 확장 설치
\c browser_agent;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
