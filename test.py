import yaml
import os
import docker
from models.container_manager import ContainerManager
from extensions import db

class DockerManager:
    """
    Utility class to manage Docker containers for different subscription plans.
    Handles container creation, status updates, and cleanup operations using Docker SDK for Python.
    """
    def __init__(self):
        """Initialize Docker client"""
        self.client = docker.from_env()

    @staticmethod
    def _get_compose_file(plan):
        """Get the Docker compose file path for a specific plan"""
        base_dir = os.path.join(os.getcwd(), f'plan_{plan}')
        compose_file = f'docker-compose_{plan}.yml'
        return os.path.join(base_dir, compose_file)

    @staticmethod
    def _is_port_available(port):
        """Check if a port is available"""
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(('', port))
            sock.close()
            return True
        except OSError:
            return False

    @staticmethod
    def _get_next_available_port(start_port=5000, max_attempts=100):
        """Find the next available port starting from start_port"""
        for port in range(start_port, start_port + max_attempts):
            if DockerManager._is_port_available(port):
                return port
        raise RuntimeError(f"No available ports found in range {start_port}-{start_port + max_attempts}")

    @staticmethod
    def _generate_unique_config(user_id):
        """Generate unique configuration for a container"""
        # Calculate port systematically (e.g., 5000 + user_id)
        port = 5000 + int(user_id)
        db_name = f'db_{user_id}'
        project_name = f'cyber_{user_id}'  # Unique project namespace
        return port, db_name, project_name

    @classmethod
    def build_main_image(cls, project):
        """
        Build the Docker image for the main app using the Dockerfile in the current directory.
        The image is tagged as {project}_cyber:latest.
        """
        client = docker.from_env()
        tag = f"{project}_cyber:latest"
        try:
            # Build the image (this may take some time)
            image, logs = client.images.build(path=".", dockerfile="Dockerfile", tag=tag, rm=True)
            # Optionally, you can log the build output:
            for chunk in logs:
                if 'stream' in chunk:
                    print(chunk['stream'].strip())
            return image
        except docker.errors.BuildError as e:
            raise Exception(f"Failed to build image for {project}: {str(e)}")
        except docker.errors.APIError as e:
            raise Exception(f"Docker API error during build for {project}: {str(e)}")

    @classmethod
    def deploy_postgres_container(cls, project, db_password, db_name):
        """
        Deploy a PostgreSQL container for the project with the specified configuration.
        Creates a shared network for communication with the main container.
        """
        client = docker.from_env()
        container_name = f"{project}_postgres"
        network_name = f"{project}_network"  # Define a common network

        # Ensure network exists
        try:
            client.networks.get(network_name)
        except docker.errors.NotFound:
            client.networks.create(network_name, driver="bridge")

        try:
            # Remove any existing container
            existing = client.containers.list(all=True, filters={"name": container_name})
            for cont in existing:
                cont.remove(force=True)
                
            volume_name = f"{project}_postgres_data"
            # Create volume if it doesn't exist
            volumes = client.volumes.list(filters={"name": volume_name})
            if not volumes:
                client.volumes.create(name=volume_name)
                
            container = client.containers.run(
                "postgres:latest",
                name=container_name,
                detach=True,
                environment={
                    "POSTGRES_USER": "postgres",
                    "POSTGRES_PASSWORD": db_password,
                    "POSTGRES_DB": db_name,
                },
                volumes={volume_name: {"bind": "/var/lib/postgresql/data", "mode": "rw"}},
                ports={'5432/tcp': None},
                restart_policy={"Name": "unless-stopped"},
                network=network_name
            )
            return container
        except docker.errors.APIError as e:
            raise Exception(f"Failed to deploy Postgres: {str(e)}")

    @classmethod
    def deploy_main_container(cls, project, host_port, db_password, db_name):
        """
        Deploy the main application container after building the image.
        Connects to the same network as the PostgreSQL container.
        """
        client = docker.from_env()
        container_name = f"{project}_cyber"
        network_name = f"{project}_network"

        # Ensure network exists
        try:
            client.networks.get(network_name)
        except docker.errors.NotFound:
            client.networks.create(network_name, driver="bridge")

        try:
            # Build the image if it doesn't exist
            try:
                client.images.get(f"{project}_cyber:latest")
            except docker.errors.ImageNotFound:
                cls.build_main_image(project)
                
            main_image = f"{project}_cyber:latest"
            
            # Remove any existing container with the same name
            existing = client.containers.list(all=True, filters={"name": container_name})
            for cont in existing:
                cont.remove(force=True)
                
            container = client.containers.run(
                main_image,
                name=container_name,
                detach=True,
                ports={5000: int(host_port)},
                environment={
                    "SQLALCHEMY_DATABASE_URI": f"postgresql://postgres:{db_password}@{project}_postgres/{db_name}",
                    "POSTGRES_HOST": f"{project}_postgres",
                    "POSTGRES_USER": "postgres",
                    "POSTGRES_PASSWORD": db_password,
                    "POSTGRES_DB": db_name,
                },
                restart_policy={"Name": "unless-stopped"},
                network=network_name
            )
            return container
        except docker.errors.APIError as e:
            raise Exception(f"Failed to deploy main app: {str(e)}")

    @classmethod
    def create_container(cls, user_id, plan):
        """
        Create a new Docker container for a user's subscription
        Returns container_id if successful, None if failed
        """
        client = docker.from_env()
        try:
            # Generate unique configurations
            port, db_name, project_name = cls._generate_unique_config(user_id)
            
            # Get the original compose file path and directory
            compose_file = cls._get_compose_file(plan)
            plan_dir = os.path.dirname(compose_file)
            
            # Deploy PostgreSQL container
            postgres_container = cls.deploy_postgres_container(
                project=project_name,
                db_password="db1",
                db_name=db_name
            )
            
            # Deploy main application container
            main_container = cls.deploy_main_container(
                project=project_name,
                host_port=port,
                db_password="db1",
                db_name=db_name
            )

            # Create container record in database for main container
            container = ContainerManager(
                user_id=user_id,
                container_id=main_container.name,
                port=port,
                db_name=db_name,
                plan=plan,
                status='running'
            )
            db.session.add(container)
            
            # Create container record in database for postgres container
            postgres_container_record = ContainerManager(
                user_id=user_id,
                container_id=postgres_container.name,
                port=5432,  # Default PostgreSQL port
                db_name=db_name,
                plan=plan,
                status='running'
            )
            db.session.add(postgres_container_record)
            db.session.commit()
            
            return main_container.name  # Return the main container name

        except Exception as e:
            print(f"Error creating container: {str(e)}")
            return None

    @classmethod
    def manage_container(cls, container_id, action):
        """
        Manage container lifecycle (start, stop, remove, restart)
        Returns True if successful, False otherwise
        """
        client = docker.from_env()
        try:
            container = ContainerManager.query.filter_by(container_id=container_id).first()
            if not container:
                return False

            docker_container = client.containers.get(container_id)
            
            if action == 'start':
                docker_container.start()
                container.update_status('running')
            elif action == 'stop':
                docker_container.stop()
                container.update_status('stopped')
            elif action == 'remove':
                docker_container.remove(force=True)
                container.update_status('removed')
                db.session.delete(container)
                db.session.commit()
            elif action == 'restart':
                docker_container.restart()
                container.update_status('running')
            
            return True

        except docker.errors.NotFound:
            # Container not found in Docker but exists in DB
            if action == 'remove' and container:
                container.update_status('removed')
                db.session.delete(container)
                db.session.commit()
                return True
            return False
        except Exception as e:
            print(f"Error managing container: {str(e)}")
            return False

    @classmethod
    def get_container_status(cls, container_id):
        """Get current status of a container using Docker SDK"""
        client = docker.from_env()
        try:
            container = client.containers.get(container_id)
            return container.status
        except docker.errors.NotFound:
            return 'not found'
        except Exception:
            return 'error'
            
    @classmethod
    def get_container_stats(cls, container_id):
        """Get container statistics (CPU, memory usage, etc.)"""
        client = docker.from_env()
        try:
            container = client.containers.get(container_id)
            stats = container.stats(stream=False)
            
            # Calculate CPU usage percentage
            cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage']['total_usage']
            system_delta = stats['cpu_stats']['system_cpu_usage'] - stats['precpu_stats']['system_cpu_usage']
            cpu_usage = 0
            if system_delta > 0:
                cpu_usage = (cpu_delta / system_delta) * 100.0
                
            # Calculate memory usage
            memory_usage = stats['memory_stats'].get('usage', 0)
            memory_limit = stats['memory_stats'].get('limit', 1)
            memory_percent = (memory_usage / memory_limit) * 100.0
            
            return {
                'cpu_percent': round(cpu_usage, 2),
                'memory_usage': round(memory_usage / (1024 * 1024), 2),  # Convert to MB
                'memory_percent': round(memory_percent, 2),
                'network_rx': stats.get('networks', {}).get('eth0', {}).get('rx_bytes', 0),
                'network_tx': stats.get('networks', {}).get('eth0', {}).get('tx_bytes', 0)
            }
        except (docker.errors.NotFound, KeyError, AttributeError):
            return None
        except Exception as e:
            print(f"Error getting container stats: {str(e)}")
            return None
            
    @classmethod
    def get_container_logs(cls, container_id, lines=5):
        """Get the last N lines of container logs"""
        client = docker.from_env()
        try:
            container = client.containers.get(container_id)
            logs = container.logs(tail=lines, stream=False).decode('utf-8')
            return logs.splitlines()
        except docker.errors.NotFound:
            return []
        except Exception as e:
            print(f"Error getting container logs: {str(e)}")
            return []
