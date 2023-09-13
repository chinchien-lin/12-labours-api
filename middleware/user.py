"""
Functionality for creating gen3 access users to support portal access control
- get_user_identity
- get_user_access_scope
- get_user_expire_time
"""


class User:
    """
    identity, access_scope, expire_time are required to create user
    """

    def __init__(self, identity, access_scope, expire_time):
        self.identity = identity
        self.access_scope = access_scope
        self.expire_time = expire_time

    def get_user_identity(self):
        """
        Handler for returning user identity
        """
        return self.identity

    def get_user_access_scope(self):
        """
        Handler for returning user access scope
        """
        return self.access_scope

    def get_user_expire_time(self):
        """
        Handler for returning user expire time
        """
        return self.expire_time
