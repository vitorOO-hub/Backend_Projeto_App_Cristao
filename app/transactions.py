"""Short database-only transactions; callbacks must be safe to run again."""
from pymongo import timeout
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern


class MongoTransactions:
    def __init__(self, database):
        self.client = database.client

    def run(self, operation):
        # The driver's retry loop also obeys this deadline. Files and network providers
        # must never be used inside an operation passed here.
        with timeout(10), self.client.start_session() as session:
            return session.with_transaction(operation, read_concern=ReadConcern('snapshot'),
                                            write_concern=WriteConcern('majority'),
                                            max_commit_time_ms=5000)
