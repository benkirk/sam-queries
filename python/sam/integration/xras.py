#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class XrasRole(Base):
    """XRAS system roles."""
    __tablename__ = 'xras_role'

    # Note: Schema structure needs verification - these are placeholder based on naming
    xras_role_id = Column(Integer, primary_key=True, autoincrement=True)
    role_name = Column(String(50), nullable=False)
    description = Column(String(255))

    users = relationship('XrasUser', back_populates='role')

    def __repr__(self):
        return f"<XrasRole(name='{self.role_name}')>"


#----------------------------------------------------------------------------
class XrasUser(Base):
    """XRAS user mappings."""
    __tablename__ = 'xras_user'

    __table_args__ = (
        Index('ix_xras_user_local', 'local_user_id'),
    )

    xras_user_id = Column(Integer, primary_key=True, autoincrement=True)
    xras_username = Column(String(50), nullable=False, unique=True)
    local_user_id = Column(Integer, ForeignKey('users.user_id'))
    xras_role_id = Column(Integer, ForeignKey('xras_role.xras_role_id'))
    active = Column(Boolean, nullable=False, default=True)
    creation_time = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    modified_time = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'), onupdate=text('CURRENT_TIMESTAMP'))

    local_user = relationship('User', back_populates='xras_user')
    role = relationship('XrasRole', back_populates='users')
    requests = relationship('XrasRequest', back_populates='user')

    def __repr__(self):
        return f"<XrasUser(username='{self.xras_username}')>"


#----------------------------------------------------------------------------
class XrasAction(Base):
    """XRAS action types."""
    __tablename__ = 'xras_action'

    xras_action_id = Column(Integer, primary_key=True, autoincrement=True)
    action_name = Column(String(50), nullable=False, unique=True)
    description = Column(String(255))

    requests = relationship('XrasRequest', back_populates='action')

    def __repr__(self):
        return f"<XrasAction(name='{self.action_name}')>"


#----------------------------------------------------------------------------
class XrasRequest(Base):
    """XRAS allocation requests."""
    __tablename__ = 'xras_request'

    __table_args__ = (
        Index('ix_xras_request_user', 'xras_user_id'),
        Index('ix_xras_request_action', 'xras_action_id'),
        Index('ix_xras_request_status', 'status'),
    )

    xras_request_id = Column(Integer, primary_key=True, autoincrement=True)
    xras_user_id = Column(Integer, ForeignKey('xras_user.xras_user_id'), nullable=False)
    xras_action_id = Column(Integer, ForeignKey('xras_action.xras_action_id'), nullable=False)

    request_number = Column(String(50), unique=True)
    status = Column(String(20), nullable=False)

    request_date = Column(DateTime, nullable=False)
    approval_date = Column(DateTime)

    request_data = Column(Text)  # JSON data

    creation_time = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    modified_time = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'), onupdate=text('CURRENT_TIMESTAMP'))

    user = relationship('XrasUser', back_populates='requests')
    action = relationship('XrasAction', back_populates='requests')
    allocations = relationship('XrasAllocation', back_populates='request')

    def __repr__(self):
        return f"<XrasRequest(number='{self.request_number}', status='{self.status}')>"


#----------------------------------------------------------------------------
class XrasAllocation(Base):
    """XRAS allocations."""
    __tablename__ = 'xras_allocation'

    __table_args__ = (
        Index('ix_xras_allocation_request', 'xras_request_id'),
        Index('ix_xras_allocation_local', 'local_allocation_id'),
    )

    xras_allocation_id = Column(Integer, primary_key=True, autoincrement=True)
    xras_request_id = Column(Integer, ForeignKey('xras_request.xras_request_id'),
                             nullable=False)
    local_allocation_id = Column(Integer, ForeignKey('allocation.allocation_id'))

    xras_allocation_number = Column(String(50), unique=True)
    amount = Column(Numeric(15, 2), nullable=False)

    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)

    creation_time = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    modified_time = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'), onupdate=text('CURRENT_TIMESTAMP'))

    request = relationship('XrasRequest', back_populates='allocations')
    local_allocation = relationship('Allocation', back_populates='xras_allocation')
    hpc_amounts = relationship('XrasHpcAllocationAmount', back_populates='xras_allocation')

    def __repr__(self):
        return f"<XrasAllocation(number='{self.xras_allocation_number}', amount={self.amount})>"


#----------------------------------------------------------------------------
class XrasHpcAllocationAmount(Base):
    """XRAS HPC allocation amounts by resource."""
    __tablename__ = 'xras_hpc_allocation_amount'

    __table_args__ = (
        Index('ix_xras_hpc_allocation', 'xras_allocation_id'),
    )

    xras_hpc_allocation_amount_id = Column(Integer, primary_key=True, autoincrement=True)
    xras_allocation_id = Column(Integer, ForeignKey('xras_allocation.xras_allocation_id'),
                                nullable=False)
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)

    xras_allocation = relationship('XrasAllocation', back_populates='hpc_amounts')
    resource = relationship('Resource', back_populates='xras_hpc_amounts')

    def __repr__(self):
        return f"<XrasHpcAllocationAmount(allocation_id={self.xras_allocation_id}, amount={self.amount})>"


#----------------------------------------------------------------------------
class XrasResourceRepositoryKeyResource(Base):
    """Maps XRAS resource repository keys to local resources."""
    __tablename__ = 'xras_resource_repository_key_resource'

    xras_resource_key_id = Column(Integer, primary_key=True, autoincrement=True)
    xras_resource_key = Column(String(100), nullable=False, unique=True)
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    creation_time = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    resource = relationship('Resource', back_populates='xras_resource_keys')

    def __repr__(self):
        return f"<XrasResourceRepositoryKeyResource(key='{self.xras_resource_key}')>"


# ============================================================================
# End of module
# ============================================================================


#-------------------------------------------------------------------------em-
