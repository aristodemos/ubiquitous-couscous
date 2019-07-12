--
-- PostgreSQL database dump
--

-- Dumped from database version 11.4 (Debian 11.4-1.pgdg90+1)
-- Dumped by pg_dump version 11.4 (Debian 11.4-1.pgdg90+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_with_oids = false;

--
-- Name: all_nodes; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.all_nodes (
    ip_address character varying(50),
    port integer,
    services character varying(50),
    first_seen bigint,
    last_seen bigint,
    counter_seen int,
    height bigint,
    version character varying(50),
    my_ip character varying(50),
    blockchain character varying(30),
    CONSTRAINT all_nodes_port_check CHECK ((port < 99999))
);


ALTER TABLE public.all_nodes OWNER TO postgres;

--
-- Name: blockheights; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.blockheights (
    id integer NOT NULL,
    "timestamp" bigint,
    height bigint,
    blockchain character varying(30)
);


ALTER TABLE public.blockheights OWNER TO postgres;

--
-- Name: blockheights_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.blockheights_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.blockheights_id_seq OWNER TO postgres;

--
-- Name: blockheights_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.blockheights_id_seq OWNED BY public.blockheights.id;


--
-- Name: pending_nodes; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.pending_nodes (
    ip_address character varying(50),
    port integer,
    services character varying(50),
    blockchain character varying(30),
    CONSTRAINT pending_nodes_port_check CHECK ((port < 99999))
);


ALTER TABLE public.pending_nodes OWNER TO postgres;

--
-- Name: updated_nodes; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.updated_nodes (
    ip_address character varying(50),
    port integer,
    services character varying(50),
    last_seen bigint,
    height bigint,
    version character varying(50),
    blockchain character varying(30),
    CONSTRAINT updated_nodes_port_check CHECK ((port < 99999))
);


ALTER TABLE public.updated_nodes OWNER TO postgres;

--
-- Name: crawl_status; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.crawl_status AS
 SELECT ( SELECT count(*) AS count
           FROM public.pending_nodes) AS pending,
    ( SELECT count(*) AS count
           FROM public.updated_nodes) AS updated;


ALTER TABLE public.crawl_status OWNER TO postgres;

--
-- Name: graph_links; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.graph_links (
    source_ip character varying(50),
    source_port integer,
    blockchain character varying(30),
    sink_ip character varying(50),
    sink_port integer,
    first_seen bigint,
    last_seen bigint,
    counter_seen int
);


ALTER TABLE public.graph_links OWNER TO postgres;

--
-- Name: master_status; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.master_status (
    id integer NOT NULL,
    status character varying NOT NULL,
    start_time bigint,
    elapsed_time bigint,
    height bigint,
    blockchain character varying(30),
    reachable_nodes integer
);


ALTER TABLE public.master_status OWNER TO postgres;

--
-- Name: master_status_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.master_status_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.master_status_id_seq OWNER TO postgres;

--
-- Name: master_status_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.master_status_id_seq OWNED BY public.master_status.id;


--
-- Name: ping_times; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.ping_times (
    ip_address character varying(50) NOT NULL,
    port integer NOT NULL,
    last_ping bigint NOT NULL,
    rtt bigint NOT NULL,
    from_ip character varying(50) NOT NULL,
    blockchain character varying(30)
);


ALTER TABLE public.ping_times OWNER TO postgres;

--
-- Name: ping_variance; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.ping_variance AS
 SELECT ping_times.ip_address,
    variance(ping_times.rtt) AS variance,
    count(ping_times.ip_address) AS ncounts
   FROM public.ping_times
  WHERE ((ping_times.ip_address)::text IN ( SELECT ping_times_1.ip_address
           FROM public.ping_times ping_times_1
          GROUP BY ping_times_1.ip_address
         HAVING (count(ping_times_1.ip_address) > 1)))
  GROUP BY ping_times.ip_address
  ORDER BY (variance(ping_times.rtt)) DESC;


ALTER TABLE public.ping_variance OWNER TO postgres;

--
-- Name: state_view; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.state_view AS
 SELECT pg_stat_activity.datname,
    pg_stat_activity.usename,
    pg_stat_activity.query,
    pg_stat_activity.query_start,
    pg_stat_activity.state_change,
    pg_stat_activity.state
   FROM pg_stat_activity;


ALTER TABLE public.state_view OWNER TO postgres;

--
-- Name: blockheights id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blockheights ALTER COLUMN id SET DEFAULT nextval('public.blockheights_id_seq'::regclass);


--
-- Name: master_status id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.master_status ALTER COLUMN id SET DEFAULT nextval('public.master_status_id_seq'::regclass);


--
-- Name: all_nodes all_nodes_unique; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.all_nodes
    ADD CONSTRAINT all_nodes_unique UNIQUE (ip_address, port, services, blockchain);


--
-- Name: blockheights blockheights_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blockheights
    ADD CONSTRAINT blockheights_pkey PRIMARY KEY (id);


--
-- Name: graph_links glinks_unique; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.graph_links
    ADD CONSTRAINT glinks_unique UNIQUE (source_ip, source_port, blockchain, sink_ip, sink_port);


--
-- Name: master_status master_status_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.master_status
    ADD CONSTRAINT master_status_pkey PRIMARY KEY (id);


--
-- Name: pending_nodes pending_nodes_unique; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.pending_nodes
    ADD CONSTRAINT pending_nodes_unique UNIQUE (ip_address, port, blockchain);


--
-- Name: updated_nodes updated_nodes_unique; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.updated_nodes
    ADD CONSTRAINT updated_nodes_unique UNIQUE (ip_address, port, blockchain);


--
-- PostgreSQL database dump complete
--

