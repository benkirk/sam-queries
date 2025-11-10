#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Role(Base):
    """Security roles in the system."""
    __tablename__ = 'role'

    role_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    description = Column(String(255))

    users = relationship('RoleUser', back_populates='role')

    def __str__(self):
        return f"{self.name}"

    def __repr__(self):
        return f"<Role(name='{self.name}')>"

    def __eq__(self, other):
        """Two roles are equal if they have the same role_id."""
        if not isinstance(other, Role):
            return False
        return self.role_id is not None and self.role_id == other.role_id

    def __hash__(self):
        """Hash based on role_id for set/dict operations."""
        return hash(self.role_id) if self.role_id is not None else hash(id(self))


#----------------------------------------------------------------------------
class RoleUser(Base):
    """Maps users to roles."""
    __tablename__ = 'role_user'

    __table_args__ = (
        Index('ix_role_user_role', 'role_id'),
        Index('ix_role_user_user', 'user_id'),
    )

    role_user_id = Column(Integer, primary_key=True, autoincrement=True)
    role_id = Column(Integer, ForeignKey('role.role_id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)

    role = relationship('Role', back_populates='users')
    user = relationship('User', back_populates='role_assignments')



# ============================================================================
# Computational Activity and Charges
# ============================================================================


#-------------------------------------------------------------------------em-
