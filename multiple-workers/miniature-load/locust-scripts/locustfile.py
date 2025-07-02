from cmath import cos
from typing import Optional, Dict, Union
from locust import HttpUser, task, between, events, SequentialTaskSet
from prometheus_client import start_http_server, Counter, Histogram
from faker import Faker
import logging
import time
import os
import json
import datetime
import random
import socket
from threading import Lock
from gevent.lock import Semaphore

import gevent

import redis

r = redis.Redis(host='172.17.0.1', port=6379, decode_responses=True)

# Configure logging
LOG_DIR = "/mnt/locust/stats"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'test_execution.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

usernames = ["alice", "bob", "carol", "dave"]
# Prometheus metrics
REQUEST_COUNT = Counter(
    'locust_request_count',
    'Number of requests made',
    ['method', 'endpoint', 'status']
)
REQUEST_LATENCY = Histogram(
    'locust_request_latency_seconds',
    'Request latency in seconds',
    ['method', 'endpoint']
)
ERROR_COUNT = Counter(
    'locust_error_count',
    'Number of errors',
    ['error_type', 'endpoint']
)

class TestConfig:
    HOSTS = [
        "http://worker1_url",
        "http://worker2_url",
        "http://worker3_url"
    ]

    REQUEST_TIMEOUT = 30
    BASE_PORT = 5000
    PORT_RANGE = 1000  # allows ports 5000–5999
    redis_host = os.getenv("REDIS_HOST", "172.17.0.1")
    redis_port = int(os.getenv("REDIS_PORT", 6379))
    redis_prefix = "locust_ports"

    redis_client = redis.StrictRedis(host=redis_host, port=redis_port, decode_responses=True)
    _used_ports_per_host: Dict[str, set] = {}
    _port_lock = Semaphore()
    _host_index = 0

    @classmethod
    def get_available_port_number(cls, host: str) -> int:
        used_key = f"{cls.redis_prefix}:{host}:used_ports"

        for _ in range(100):  # limit retries
            port = cls.BASE_PORT + random.randint(0, cls.PORT_RANGE - 1)
            if not cls.redis_client.sismember(used_key, port):
                cls.redis_client.sadd(used_key, port)
                logger.info(f"[Redis] Assigned port {port} for host {host}")
                return port

        logger.warning(f"[Redis] No available ports left for host: {host}")
        return 0

    @classmethod
    def release_port(cls, host: str, port: int):
        used_key = f"{cls.redis_prefix}:{host}:used_ports"
        cls.redis_client.srem(used_key, port)

    @classmethod
    def get_next_host(cls) -> str:
        if not hasattr(cls, "_host_index"):
            cls._host_index = 0
        host = cls.HOSTS[cls._host_index]
        cls._host_index = (cls._host_index + 1) % len(cls.HOSTS)
        return host

class UserBehavior(SequentialTaskSet):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connection_id = None
        self.port_first = 5000
        self.port = self.port_first
        self.user_id = 0
        self.user_count = 0
        self.user_count_lock = Lock()
        self.total_port = 0
        self.username = ''
        self.port = ''
        self.current_host = ''

    def reset_state(self):
        """Reset all state flags"""
        self.connection_created = False
        self.connections_created = False
        self.invite_created = False
        self.studentinvite_created = False
        self.agent_initialized = False
        self.message_sent = False
        self.agent_name = ''
        self.invitation_url = ''
        self.current_host = TestConfig.get_next_host()

    
    def on_start(self):
        with self.user_count_lock:
            self.user_id = self.user_count
            self.user_count += 1
        self.worker_id = int(getattr(self.user.environment.runner, "worker_id", -1))
        self.reset_state()
        logger.info(f"Starting user with user_id: {self.user_id} and host {self.current_host}")    
        print(f"Starting user with user_id: {self.user_id}")
    def handle_request(self, method: str, endpoint: str, payload: Dict = None, 
                      name: str = None) -> tuple[bool, Union[Dict, None]]:
        """Request handler with detailed logging"""
        url = f"{self.current_host}{endpoint}"
        try:
            with self.client.request(
                method,
                url,
                json=payload,
                catch_response=True,
                name=name or f"{method} {endpoint}",
                timeout=TestConfig.REQUEST_TIMEOUT
            ) as response:
                logger.debug(f"Request: {method} {endpoint}")
                logger.debug(f"Payload: {payload}")
                logger.debug(f"Response Status: {response.status_code}")
                logger.debug(f"Response Body: {response.text[:500]}")

                if response.status_code in [200, 201]:
                    response.success()
                    logger.info(f"Request successful: {method} {endpoint}")
                    try:
                        if response.text:
                            return True, response.json()
                        return True, None
                    except ValueError:
                        return True, {"raw_response": response.text}
                else:
                    error_msg = f"Request failed: {response.status_code}"
                    response.failure(error_msg)
                    return False, None

        except Exception as e:
            logger.error(f"Error in {method} {endpoint}: {str(e)}")
            ERROR_COUNT.labels(
                error_type=type(e).__name__,
                endpoint=endpoint
            ).inc()
            return False, None

    @task
    def step1_agent_init(self):
        """Step 1: POST /agent/init"""
        self.port = TestConfig.get_available_port_number(self.current_host)
        self.agent_name = "pid_" + str(os.getpid()) + "time_" + datetime.datetime.now().strftime("%s%f")[:13]
        payload = {
            "agentName": self.agent_name,
            "port": self.port
        }
        logger.info(f"[PID {os.getpid()} | Worker {self.worker_id}] Assigned port {self.port} to agent {self.agent_name} on host {self.current_host}")
        success, _ = self.handle_request("POST", "/agent/init", payload)
        self.agent_initialized = success

    @task
    def step2_post_connection_invite(self):
        """Step 2: POST /connection/createinvite"""
        if not self.agent_initialized:
            return

        payload = {"agentName": self.agent_name}
        success, response_data = self.handle_request("POST", "/connection/createinvite", payload)
        self.invite_created = success
        if success and response_data:
            try:
                logger.info(f"createinvite response_data: {response_data}")
                self.invitation_url = response_data['invitation_url']
                logger.info(f"createinvite response_data invitation_url: {self.invitation_url}")
                #get connection Id where 

            except Exception as e:
                logger.error(f"Error processing createinvite response: {str(e)}")

    @task
    def step3_post_connection_invite(self):
        """Step 3: POST /connection/studentinvite"""
        if not self.invite_created:
            return

        payload = {
            "agentName": self.agent_name,
            "invitation_url": self.invitation_url}
        success, _ = self.handle_request("POST", "/connection/studentinvite", payload)
        self.studentinvite_created = success

    @task
    def step4_post_connection_connections(self):
        """Step 4: POST /connection/connections"""
        if not self.studentinvite_created:
            return

        payload = {"agentName": self.agent_name}
        success, response_data = self.handle_request("POST", "/connection/connections", payload)
        self.connections_created = success

        if success and response_data:
            try:
                logger.info(f"connections response_data: {response_data}")
                self.connection_id = [item["id"] for item in response_data if item["theirLabel"] != "Cloud Mediator"][0]
                logger.info(f"connections response_data: {self.connection_id}")
                #get connection Id where 

                if self.connection_id:
                    logger.info(f"Extracted connection ID: {self.connection_id}")
                    #with open(os.path.join(LOG_DIR, 'connection_ids.log'), 'a') as f:
                    #    f.write(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')} - Connection ID: {self.connection_id}\n")
            except Exception as e:
                logger.error(f"Error processing student invite response: {str(e)}")

    @task
    def step5_post_message_sendmessage(self):
        """Step 5: POST root menu sendmessage"""
        if not self.connections_created or not self.connection_id:
            return

        payload = {
            "connectionID": self.connection_id,
            "messageBody": "{\"workflowID\":\"root-menu\",\"actionID\":\"studentTranscriptButtonPCS\",\"data\":{}}", 
            "agentName": self.agent_name
        }
        success, _ = self.handle_request("POST", "/message/sendmessage", payload)
        if success:
            logger.info(f"Message sent successfully for connection ID: {self.connection_id}")
            self.message_sent = True
            self.reset_state()
            
class ApiTestUser(HttpUser):
    tasks = [UserBehavior]
    wait_time = between(3, 5)
    host = TestConfig.HOSTS[0]
    user_index = 0
    user_lock = Lock()
    def on_start(self):
        self.client.headers.update({
            'accept': 'application/json',
            'Content-Type': 'application/json',
            'Connection': 'keep-alive'
        })
        with ApiTestUser.user_lock:
            if ApiTestUser.user_index < len(usernames):
                self.username = usernames[ApiTestUser.user_index]
                ApiTestUser.user_index += 1
            else:
                self.username = f"user_{ApiTestUser.user_index}"
                ApiTestUser.user_index += 1

        logger.info("Started test user")

@events.init.add_listener
def on_locust_init(environment, **kwargs):
    if not environment.web_ui:
        return
    
    try:
        prometheus_port = int(os.getenv("PROMETHEUS_METRICS_PORT", 8080))
        start_http_server(prometheus_port)
        logger.info(f"Started Prometheus metrics server on port {prometheus_port}")
    except Exception as e:
        logger.error(f"Failed to start Prometheus metrics server: {e}")