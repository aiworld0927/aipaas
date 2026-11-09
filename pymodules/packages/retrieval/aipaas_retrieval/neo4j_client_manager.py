from neo4j import AsyncGraphDatabase, exceptions

from aipaas_common.conf.app_config import Neo4jConfig, app_config


class Neo4jClientManager:
    def __init__(neo4j_config:Neo4jConfig):
        self.neo4j_config = neo4j_config
        self.driver = None

    def _get_url(self):
        return f"bolt: //{self.neo4j_config.host}:{self.neo4j_config.port}"

    def init(self):
        self.driver = AsyncGraphDatabase.driver(self._get_url(),auth=(self.neo4j_config.user, self.neo4j_config.password))

    async def close(self):
        await self.driver.close()

neo4j_client_manager = Neo4jClientManager(app_config.neo4j_config)
neo4j_client_manager.init()

async def test_neo4j_connection():
    try:
        driver = neo4j_client_manager.driver
        async with driver.session() as session:
            result = await session.run("RETURN 1 AS test")
            record = await result.single()
            if record and record["test"] == 1:
                return {"connected": True, "message": "Neo4j connection successful"}
            else:
                return {"connected": False, "message": "Neo4j query failed"}
    except exceptions.AuthError:
        return {"connected": False, "message": "Neo4j authentication failed"}
    except exceptions.ServiceUnavailable:
        return {"connected": False, "message": "Neo4j service unavailable"}
    except Exception as e:
        return {"connected": False, "message": f"Neo4j connection error: {str(e)}"}