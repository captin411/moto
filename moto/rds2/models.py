from __future__ import unicode_literals

import copy

import boto.rds2
import json
from jinja2 import Template
from re import compile as re_compile
from moto.cloudformation.exceptions import UnformattedGetAttTemplateException
from moto.core import BaseBackend
from moto.core.utils import get_random_hex
from moto.ec2.models import ec2_backends
from .exceptions import RDSClientError, DBInstanceNotFoundError, DBSecurityGroupNotFoundError, DBSubnetGroupNotFoundError


class Database(object):
    def __init__(self, **kwargs):
        self.status = "available"
        self.is_replica = False
        self.replicas = []
        self.region = kwargs.get('region')
        self.engine = kwargs.get("engine")
        self.engine_version = kwargs.get("engine_version", None)
        self.default_engine_versions = {"MySQL": "5.6.21",
                                        "mysql": "5.6.21",
                                        "oracle-se1": "11.2.0.4.v3",
                                        "oracle-se": "11.2.0.4.v3",
                                        "oracle-ee": "11.2.0.4.v3",
                                        "sqlserver-ee": "11.00.2100.60.v1",
                                        "sqlserver-se": "11.00.2100.60.v1",
                                        "sqlserver-ex": "11.00.2100.60.v1",
                                        "sqlserver-web": "11.00.2100.60.v1",
                                        "postgres": "9.3.3"
                                        }
        if not self.engine_version and self.engine in self.default_engine_versions:
            self.engine_version = self.default_engine_versions[self.engine]
        self.iops = kwargs.get("iops")
        self.storage_type = kwargs.get("storage_type")
        self.master_username = kwargs.get('master_username')
        self.master_user_password = kwargs.get('master_user_password')
        self.auto_minor_version_upgrade = kwargs.get('auto_minor_version_upgrade')
        if self.auto_minor_version_upgrade is None:
            self.auto_minor_version_upgrade = True
        self.allocated_storage = kwargs.get('allocated_storage')
        self.db_instance_identifier = kwargs.get('db_instance_identifier')
        self.source_db_identifier = kwargs.get("source_db_identifier")
        self.db_instance_class = kwargs.get('db_instance_class')
        self.port = kwargs.get('port')
        self.db_instance_identifier = kwargs.get('db_instance_identifier')
        self.db_name = kwargs.get("db_name")
        self.publicly_accessible = kwargs.get("publicly_accessible")
        if self.publicly_accessible is None:
            self.publicly_accessible = True
        self.backup_retention_period = kwargs.get("backup_retention_period")
        if self.backup_retention_period is None:
            self.backup_retention_period = 1
        self.availability_zone = kwargs.get("availability_zone")
        self.multi_az = kwargs.get("multi_az")
        self.db_subnet_group_name = kwargs.get("db_subnet_group_name")
        if self.db_subnet_group_name:
            self.db_subnet_group = rds2_backends[self.region].describe_subnet_groups(self.db_subnet_group_name)[0]
        else:
            self.db_subnet_group = []
        self.db_security_groups = kwargs.get('security_groups', ['a'])
        self.vpc_security_group_ids = kwargs.get('vpc_security_group_ids', [])
        self.preferred_maintenance_window = kwargs.get('preferred_maintenance_window', 'wed:06:38-wed:07:08')
        self.db_parameter_group_name = kwargs.get('db_parameter_group_name', None)
        self.default_parameter_groups = {"MySQL": "default.mysql5.6",
                                         "mysql": "default.mysql5.6",
                                         "postgres": "default.postgres9.3"
                                         }
        if not self.db_parameter_group_name and self.engine in self.default_parameter_groups:
            self.db_parameter_group_name = self.default_parameter_groups[self.engine]

        self.preferred_backup_window = kwargs.get('preferred_backup_window', '13:14-13:44')
        self.license_model = kwargs.get('license_model', 'general-public-license')
        self.option_group_name = kwargs.get('option_group_name', None)
        self.default_option_groups = {"MySQL": "default.mysql5.6",
                                      "mysql": "default.mysql5.6",
                                      "postgres": "default.postgres9.3"
                                      }
        if not self.option_group_name and self.engine in self.default_option_groups:
            self.option_group_name = self.default_option_groups[self.engine]
        self.character_set_name = kwargs.get('character_set_name', None)
        self.tags = kwargs.get('tags', [])

    @property
    def address(self):
        return "{0}.aaaaaaaaaa.{1}.rds.amazonaws.com".format(self.db_instance_identifier, self.region)

    # TODO: confirm how this should represent in the RESULT JSON
    def add_replica(self, replica):
        self.replicas.append(replica.db_instance_identifier)

    def remove_replica(self, replica):
        self.replicas.remove(replica.db_instance_identifier)

    # TODO: confirm how this should represent in the RESULT JSON
    def set_as_replica(self):
        self.is_replica = True
        self.replicas = []

    def update(self, db_kwargs):
        for key, value in db_kwargs.items():
            if value is not None:
                setattr(self, key, value)

    def get_cfn_attribute(self, attribute_name):
        if attribute_name == 'Endpoint.Address':
            return self.address
        elif attribute_name == 'Endpoint.Port':
            return self.port
        raise UnformattedGetAttTemplateException()

    @classmethod
    def create_from_cloudformation_json(cls, resource_name, cloudformation_json, region_name):
        properties = cloudformation_json['Properties']

        db_instance_identifier = properties.get('DBInstanceIdentifier')
        if not db_instance_identifier:
            db_instance_identifier = resource_name.lower() + get_random_hex(12)
        db_security_groups = properties.get('DBSecurityGroups')
        if not db_security_groups:
            db_security_groups = []
        security_groups = [group.group_name for group in db_security_groups]
        db_subnet_group = properties.get("DBSubnetGroupName")
        db_subnet_group_name = db_subnet_group.subnet_name if db_subnet_group else None
        db_kwargs = {
            "auto_minor_version_upgrade": properties.get('AutoMinorVersionUpgrade'),
            "allocated_storage": properties.get('AllocatedStorage'),
            "availability_zone": properties.get("AvailabilityZone"),
            "backup_retention_period": properties.get("BackupRetentionPeriod"),
            "db_instance_class": properties.get('DBInstanceClass'),
            "db_instance_identifier": db_instance_identifier,
            "db_name": properties.get("DBName"),
            "db_subnet_group_name": db_subnet_group_name,
            "engine": properties.get("Engine"),
            "engine_version": properties.get("EngineVersion"),
            "iops": properties.get("Iops"),
            "master_password": properties.get('MasterUserPassword'),
            "master_username": properties.get('MasterUsername'),
            "multi_az": properties.get("MultiAZ"),
            "port": properties.get('Port', 3306),
            "publicly_accessible": properties.get("PubliclyAccessible"),
            "region": region_name,
            "security_groups": security_groups,
            "storage_type": properties.get("StorageType"),
        }

        rds2_backend = rds2_backends[region_name]
        source_db_identifier = properties.get("SourceDBInstanceIdentifier")
        if source_db_identifier:
            # Replica
            db_kwargs["source_db_identifier"] = source_db_identifier.db_instance_identifier
            database = rds2_backend.create_database_replica(db_kwargs)
        else:
            database = rds2_backend.create_database(db_kwargs)
        return database

    def to_json(self):
        template = Template("""{
        "AllocatedStorage": 10,
        "AutoMinorVersionUpgrade": "{{ database.auto_minor_version_upgrade }}",
        "AvailabilityZone": "{{ database.availability_zone }}",
        "BackupRetentionPeriod": "{{ database.backup_retention_period }}",
        "CharacterSetName": {%- if database.character_set_name -%}{{ database.character_set_name }}{%- else %} null{%- endif -%},
        "DBInstanceClass": "{{ database.db_instance_class }}",
        "DBInstanceIdentifier": "{{ database.db_instance_identifier }}",
        "DBInstanceStatus": "{{ database.status }}",
        "DBName": {%- if database.db_name -%}{{ database.db_name }}{%- else %} null{%- endif -%},
        {% if database.db_parameter_group_name -%}"DBParameterGroups": {
            "DBParameterGroup": {
            "ParameterApplyStatus": "in-sync",
            "DBParameterGroupName": "{{ database.db_parameter_group_name }}"
          }
        },{%- endif %}
        "DBSecurityGroups": [{
          {% for security_group in database.db_security_groups -%}{%- if loop.index != 1 -%},{%- endif -%}
          "DBSecurityGroup": {
            "Status": "active",
            "DBSecurityGroupName": "{{ security_group }}"
          }{% endfor %}
        }],{%- if database.db_subnet_group -%}
        "DBSubnetGroup": {
            "DBSubnetGroupDescription": "nabil-db-subnet-group",
            "DBSubnetGroupName": "nabil-db-subnet-group",
            "SubnetGroupStatus": "Complete",
            "Subnets": [
                {
                    "SubnetAvailabilityZone": {
                        "Name": "us-west-2c",
                        "ProvisionedIopsCapable": false
                    },
                    "SubnetIdentifier": "subnet-c0ea0099",
                    "SubnetStatus": "Active"
                },
                {
                    "SubnetAvailabilityZone": {
                        "Name": "us-west-2a",
                        "ProvisionedIopsCapable": false
                    },
                    "SubnetIdentifier": "subnet-ff885d88",
                    "SubnetStatus": "Active"
                }
            ],
            "VpcId": "vpc-8e6ab6eb"
        },{%- endif %}
        "Engine": "{{ database.engine }}",
        "EngineVersion": "{{ database.engine_version }}",
        "LatestRestorableTime": null,
        "LicenseModel": "{{ database.license_model }}",
        "MasterUsername": "{{ database.master_username }}",
        "MultiAZ": "{{ database.multi_az }}",{% if database.option_group_name %}
        "OptionGroupMemberships": [{
          "OptionGroupMembership": {
            "OptionGroupName": "{{ database.option_group_name }}",
            "Status": "in-sync"
          }
        }],{%- endif %}
        "PendingModifiedValues": { "MasterUserPassword": "****" },
        "PreferredBackupWindow": "{{ database.preferred_backup_window }}",
        "PreferredMaintenanceWindow": "{{ database.preferred_maintenance_window }}",
        "PubliclyAccessible": "{{ database.publicly_accessible }}",
        "AllocatedStorage": "{{ database.allocated_storage }}",
        "Endpoint": {
            "Address": "{{ database.address }}",
            "Port": "{{ database.port }}"
        },
        "InstanceCreateTime": null,
        "Iops": null,
        "ReadReplicaDBInstanceIdentifiers": [{%- for replica in database.replicas -%}
            {%- if not loop.first -%},{%- endif -%}
            "{{ replica }}"
        {%- endfor -%}
        ],
        "ReadReplicaSourceDBInstanceIdentifier": null,
        "SecondaryAvailabilityZone": null,
        "StatusInfos": null,
        "VpcSecurityGroups": [
            {
                "Status": "active",
                "VpcSecurityGroupId": "sg-123456"
            }
        ]
      }""")
        return template.render(database=self)

    def get_tags(self):
        return self.tags

    def add_tags(self, tags):
        new_keys = [tag_set['Key'] for tag_set in tags]
        self.tags = [tag_set for tag_set in self.tags if tag_set['Key'] not in new_keys]
        self.tags.extend(tags)
        return self.tags

    def remove_tags(self, tag_keys):
        self.tags = [tag_set for tag_set in self.tags if tag_set['Key'] not in tag_keys]


class SecurityGroup(object):
    def __init__(self, group_name, description):
        self.group_name = group_name
        self.description = description
        self.status = "authorized"
        self.ip_ranges = []
        self.ec2_security_groups = []
        self.tags = []
        self.owner_id = '1234567890'
        self.vpc_id = None

    def to_xml(self):
        template = Template("""<DBSecurityGroup>
            <EC2SecurityGroups>
            {% for security_group in security_group.ec2_security_groups %}
                <EC2SecurityGroup>
                    <EC2SecurityGroupId>{{ security_group.id }}</EC2SecurityGroupId>
                    <EC2SecurityGroupName>{{ security_group.name }}</EC2SecurityGroupName>
                    <EC2SecurityGroupOwnerId>{{ security_group.owner_id }}</EC2SecurityGroupOwnerId>
                    <Status>authorized</Status>
                </EC2SecurityGroup>
            {% endfor %}
            </EC2SecurityGroups>

            <DBSecurityGroupDescription>{{ security_group.description }}</DBSecurityGroupDescription>
            <IPRanges>
            {% for ip_range in security_group.ip_ranges %}
                <IPRange>
                    <CIDRIP>{{ ip_range }}</CIDRIP>
                    <Status>authorized</Status>
                </IPRange>
            {% endfor %}
            </IPRanges>
            <OwnerId>{{ security_group.ownder_id }}</OwnerId>
            <DBSecurityGroupName>{{ security_group.group_name }}</DBSecurityGroupName>
        </DBSecurityGroup>""")
        return template.render(security_group=self)

    def to_json(self):
        template = Template("""{
            "DBSecurityGroupDescription": "{{ security_group.description }}",
            "DBSecurityGroupName": "{{ security_group.group_name }}",
            "EC2SecurityGroups": {{ security_group.ec2_security_groups }},
            "IPRanges": [{%- for ip in security_group.ip_ranges -%}
                         {%- if loop.index != 1 -%},{%- endif -%}
                         "{{ ip }}"
                         {%- endfor -%}
                        ],
            "OwnerId": "{{ security_group.owner_id }}",
            "VpcId": "{{ security_group.vpc_id }}"
        }""")
        return template.render(security_group=self)

    def authorize_cidr(self, cidr_ip):
        self.ip_ranges.append(cidr_ip)

    def authorize_security_group(self, security_group):
        self.ec2_security_groups.append(security_group)

    @classmethod
    def create_from_cloudformation_json(cls, resource_name, cloudformation_json, region_name):
        properties = cloudformation_json['Properties']
        group_name = resource_name.lower() + get_random_hex(12)
        description = properties['GroupDescription']
        security_group_ingress = properties['DBSecurityGroupIngress']

        ec2_backend = ec2_backends[region_name]
        rds2_backend = rds2_backends[region_name]
        security_group = rds2_backend.create_security_group(
            group_name,
            description,
        )
        for ingress_type, ingress_value in security_group_ingress.items():
            if ingress_type == "CIDRIP":
                security_group.authorize_cidr(ingress_value)
            elif ingress_type == "EC2SecurityGroupName":
                subnet = ec2_backend.get_security_group_from_name(ingress_value)
                security_group.authorize_security_group(subnet)
            elif ingress_type == "EC2SecurityGroupId":
                subnet = ec2_backend.get_security_group_from_id(ingress_value)
                security_group.authorize_security_group(subnet)
        return security_group

    def get_tags(self):
        # TODO: Write tags add/remove/list tests for SecurityGroups
        return self.tags

    def add_tags(self, tags):
        new_keys = [tag_set['Key'] for tag_set in tags]
        self.tags = [tag_set for tag_set in self.tags if tag_set['Key'] not in new_keys]
        self.tags.extend(tags)
        return self.tags

    def remove_tags(self, tag_keys):
        self.tags = [tag_set for tag_set in self.tags if tag_set['Key'] not in tag_keys]


class SubnetGroup(object):
    def __init__(self, subnet_name, description, subnets):
        self.subnet_name = subnet_name
        self.description = description
        self.subnets = subnets
        self.status = "Complete"
        self.tags = []
        self.vpc_id = self.subnets[0].vpc_id

    def to_xml(self):
        template = Template("""<DBSubnetGroup>
              <VpcId>{{ subnet_group.vpc_id }}</VpcId>
              <SubnetGroupStatus>{{ subnet_group.status }}</SubnetGroupStatus>
              <DBSubnetGroupDescription>{{ subnet_group.description }}</DBSubnetGroupDescription>
              <DBSubnetGroupName>{{ subnet_group.subnet_name }}</DBSubnetGroupName>
              <Subnets>
                {% for subnet in subnet_group.subnets %}
                <Subnet>
                  <SubnetStatus>Active</SubnetStatus>
                  <SubnetIdentifier>{{ subnet.id }}</SubnetIdentifier>
                  <SubnetAvailabilityZone>
                    <Name>{{ subnet.availability_zone }}</Name>
                    <ProvisionedIopsCapable>false</ProvisionedIopsCapable>
                  </SubnetAvailabilityZone>
                </Subnet>
                {% endfor %}
              </Subnets>
            </DBSubnetGroup>""")
        return template.render(subnet_group=self)

    def to_json(self):
        template = Template("""{
            "DBSubnetGroup": {
                "VpcId": "{{ subnet_group.vpc_id }}",
                "SubnetGroupStatus": "{{ subnet_group.status }}",
                "DBSubnetGroupDescription": "{{ subnet_group.description }}",
                "DBSubnetGroupName": "{{ subnet_group.subnet_name }}",
                "Subnets": {
                  "Subnet": [
                    {% for subnet in subnet_group.subnets %}{
                      "SubnetStatus": "Active",
                      "SubnetIdentifier": "{{ subnet.id }}",
                      "SubnetAvailabilityZone": {
                        "Name": "{{ subnet.availability_zone }}",
                        "ProvisionedIopsCapable": "false"
                      }
                    }{%- if not loop.last -%},{%- endif -%}{% endfor %}
                  ]
                }
            }
          }""")
        return template.render(subnet_group=self)

    @classmethod
    def create_from_cloudformation_json(cls, resource_name, cloudformation_json, region_name):
        properties = cloudformation_json['Properties']

        subnet_name = resource_name.lower() + get_random_hex(12)
        description = properties['DBSubnetGroupDescription']
        subnet_ids = properties['SubnetIds']

        ec2_backend = ec2_backends[region_name]
        subnets = [ec2_backend.get_subnet(subnet_id) for subnet_id in subnet_ids]
        rds2_backend = rds2_backends[region_name]
        subnet_group = rds2_backend.create_subnet_group(
            subnet_name,
            description,
            subnets,
        )
        return subnet_group

    def get_tags(self):
        # TODO: Write tags add/remove/list tests for SubnetGroups
        return self.tags

    def add_tags(self, tags):
        new_keys = [tag_set['Key'] for tag_set in tags]
        self.tags = [tag_set for tag_set in self.tags if tag_set['Key'] not in new_keys]
        self.tags.extend(tags)
        return self.tags

    def remove_tags(self, tag_keys):
        self.tags = [tag_set for tag_set in self.tags if tag_set['Key'] not in tag_keys]


class RDS2Backend(BaseBackend):

    def __init__(self):
        self.arn_regex = re_compile(r'^arn:aws:rds:.*:[0-9]*:(db|es|og|pg|ri|secgrp|snapshot|subgrp):.*$')
        self.databases = {}
        self.security_groups = {}
        self.subnet_groups = {}
        self.option_groups = {}

    def create_database(self, db_kwargs):
        database_id = db_kwargs['db_instance_identifier']
        database = Database(**db_kwargs)
        self.databases[database_id] = database
        return database

    def create_database_replica(self, db_kwargs):
        database_id = db_kwargs['db_instance_identifier']
        source_database_id = db_kwargs['source_db_identifier']
        primary = self.describe_databases(source_database_id)[0]
        replica = copy.deepcopy(primary)
        replica.update(db_kwargs)
        replica.set_as_replica()
        self.databases[database_id] = replica
        primary.add_replica(replica)
        return replica

    def describe_databases(self, db_instance_identifier=None):
        if db_instance_identifier:
            if db_instance_identifier in self.databases:
                return [self.databases[db_instance_identifier]]
            else:
                raise DBInstanceNotFoundError(db_instance_identifier)
        return self.databases.values()

    def modify_database(self, db_instance_identifier, db_kwargs):
        database = self.describe_databases(db_instance_identifier)[0]
        database.update(db_kwargs)
        return database

    def reboot_db_instance(self, db_instance_identifier):
        database = self.describe_databases(db_instance_identifier)[0]
        return database

    def delete_database(self, db_instance_identifier):
        if db_instance_identifier in self.databases:
            database = self.databases.pop(db_instance_identifier)
            if database.is_replica:
                primary = self.describe_databases(database.source_db_identifier)[0]
                primary.remove_replica(database)
            return database
        else:
            raise DBInstanceNotFoundError(db_instance_identifier)

    def create_security_group(self, group_name, description):
        security_group = SecurityGroup(group_name, description)
        self.security_groups[group_name] = security_group
        return security_group

    def describe_security_groups(self, security_group_name):
        if security_group_name:
            if security_group_name in self.security_groups:
                return [self.security_groups[security_group_name]]
            else:
                raise DBSecurityGroupNotFoundError(security_group_name)
        return self.security_groups.values()

    def delete_security_group(self, security_group_name):
        if security_group_name in self.security_groups:
            return self.security_groups.pop(security_group_name)
        else:
            raise DBSecurityGroupNotFoundError(security_group_name)

    def authorize_security_group(self, security_group_name, cidr_ip):
        security_group = self.describe_security_groups(security_group_name)[0]
        security_group.authorize_cidr(cidr_ip)
        return security_group

    def create_subnet_group(self, subnet_name, description, subnets):
        subnet_group = SubnetGroup(subnet_name, description, subnets)
        self.subnet_groups[subnet_name] = subnet_group
        return subnet_group

    def describe_subnet_groups(self, subnet_group_name):
        if subnet_group_name:
            if subnet_group_name in self.subnet_groups:
                return [self.subnet_groups[subnet_group_name]]
            else:
                raise DBSubnetGroupNotFoundError(subnet_group_name)
        return self.subnet_groups.values()

    def delete_subnet_group(self, subnet_name):
        if subnet_name in self.subnet_groups:
            return self.subnet_groups.pop(subnet_name)
        else:
            raise DBSubnetGroupNotFoundError(subnet_name)

    def create_option_group(self, option_group_kwargs):
        option_group_id = option_group_kwargs['name']
        valid_option_group_engines = {'mysql': ['5.6'],
                                      'oracle-se1': ['11.2'],
                                      'oracle-se': ['11.2'],
                                      'oracle-ee': ['11.2'],
                                      'sqlserver-se': ['10.50', '11.00'],
                                      'sqlserver-ee': ['10.50', '11.00']
                                      }
        if option_group_kwargs['name'] in self.option_groups:
            raise RDSClientError('OptionGroupAlreadyExistsFault',
                                 'An option group named {} already exists.'.format(option_group_kwargs['name']))
        if 'description' not in option_group_kwargs or not option_group_kwargs['description']:
            raise RDSClientError('InvalidParameterValue',
                                 'The parameter OptionGroupDescription must be provided and must not be blank.')
        if option_group_kwargs['engine_name'] not in valid_option_group_engines.keys():
            raise RDSClientError('InvalidParameterValue', 'Invalid DB engine: non-existant')
        if option_group_kwargs['major_engine_version'] not in\
           valid_option_group_engines[option_group_kwargs['engine_name']]:
                raise RDSClientError('InvalidParameterCombination',
                                     'Cannot find major version {0} for {1}'.format(
                                         option_group_kwargs['major_engine_version'],
                                         option_group_kwargs['engine_name']
                                     ))
        option_group = OptionGroup(**option_group_kwargs)
        self.option_groups[option_group_id] = option_group
        return option_group

    def delete_option_group(self, option_group_name):
        if option_group_name in self.option_groups:
            return self.option_groups.pop(option_group_name)
        else:
            raise RDSClientError('OptionGroupNotFoundFault', 'Specified OptionGroupName: {} not found.'.format(option_group_name))

    def describe_option_groups(self, option_group_kwargs):
        option_group_list = []

        if option_group_kwargs['marker']:
            marker = option_group_kwargs['marker']
        else:
            marker = 0
        if option_group_kwargs['max_records']:
            if option_group_kwargs['max_records'] < 20 or option_group_kwargs['max_records'] > 100:
                raise RDSClientError('InvalidParameterValue',
                                     'Invalid value for max records. Must be between 20 and 100')
            max_records = option_group_kwargs['max_records']
        else:
            max_records = 100

        for option_group_name, option_group in self.option_groups.items():
            if option_group_kwargs['name'] and option_group.name != option_group_kwargs['name']:
                continue
            elif option_group_kwargs['engine_name'] and \
                    option_group.engine_name != option_group_kwargs['engine_name']:
                continue
            elif option_group_kwargs['major_engine_version'] and \
                    option_group.major_engine_version != option_group_kwargs['major_engine_version']:
                continue
            else:
                option_group_list.append(option_group)
        if not len(option_group_list):
            raise RDSClientError('OptionGroupNotFoundFault',
                                 'Specified OptionGroupName: {} not found.'.format(option_group_kwargs['name']))
        return option_group_list[marker:max_records+marker]

    @staticmethod
    def describe_option_group_options(engine_name, major_engine_version=None):
        default_option_group_options = {
            'mysql': {'all': '{"DescribeOptionGroupOptionsResponse": {"DescribeOptionGroupOptionsResult": {"Marker": null, "OptionGroupOptions": [{"MinimumRequiredMinorEngineVersion": "12", "OptionsDependedOn": [], "MajorEngineVersion": "5.6", "Persistent": false, "DefaultPort": 11211, "Permanent": false, "OptionGroupOptionSettings": [{"SettingDescription": "Specifies how many memcached read operations (get) to perform before doing a COMMIT to start a new transaction", "DefaultValue": "1", "AllowedValues": "1-4294967295", "IsModifiable": true, "SettingName": "DAEMON_MEMCACHED_R_BATCH_SIZE", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies how many memcached write operations, such as add, set, or incr, to perform before doing a COMMIT to start a new transaction", "DefaultValue": "1", "AllowedValues": "1-4294967295", "IsModifiable": true, "SettingName": "DAEMON_MEMCACHED_W_BATCH_SIZE", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies how often to auto-commit idle connections that use the InnoDB memcached interface.", "DefaultValue": "5", "AllowedValues": "1-1073741824", "IsModifiable": true, "SettingName": "INNODB_API_BK_COMMIT_INTERVAL", "ApplyType": "DYNAMIC"}, {"SettingDescription": "Disables the use of row locks when using the InnoDB memcached interface.", "DefaultValue": "0", "AllowedValues": "0,1", "IsModifiable": true, "SettingName": "INNODB_API_DISABLE_ROWLOCK", "ApplyType": "STATIC"}, {"SettingDescription": "Locks the table used by the InnoDB memcached plugin, so that it cannot be dropped or altered by DDL through the SQL interface.", "DefaultValue": "0", "AllowedValues": "0,1", "IsModifiable": true, "SettingName": "INNODB_API_ENABLE_MDL", "ApplyType": "STATIC"}, {"SettingDescription": "Lets you control the transaction isolation level on queries processed by the memcached interface.", "DefaultValue": "0", "AllowedValues": "0-3", "IsModifiable": true, "SettingName": "INNODB_API_TRX_LEVEL", "ApplyType": "STATIC"}, {"SettingDescription": "The binding protocol to use which can be either auto, ascii, or binary. The default is auto which means the server automatically negotiates the protocol with the client.", "DefaultValue": "auto", "AllowedValues": "auto,ascii,binary", "IsModifiable": true, "SettingName": "BINDING_PROTOCOL", "ApplyType": "STATIC"}, {"SettingDescription": "The backlog queue configures how many network connections can be waiting to be processed by memcached", "DefaultValue": "1024", "AllowedValues": "1-2048", "IsModifiable": true, "SettingName": "BACKLOG_QUEUE_LIMIT", "ApplyType": "STATIC"}, {"SettingDescription": "Disable the use of compare and swap (CAS) which reduces the per-item size by 8 bytes.", "DefaultValue": "0", "AllowedValues": "0,1", "IsModifiable": true, "SettingName": "CAS_DISABLED", "ApplyType": "STATIC"}, {"SettingDescription": "Minimum chunk size in bytes to allocate for the smallest item\'s key, value, and flags. The default is 48 and you can get a significant memory efficiency gain with a lower value.", "DefaultValue": "48", "AllowedValues": "1-48", "IsModifiable": true, "SettingName": "CHUNK_SIZE", "ApplyType": "STATIC"}, {"SettingDescription": "Chunk size growth factor that controls the size of each successive chunk with each chunk growing times this amount larger than the previous chunk.", "DefaultValue": "1.25", "AllowedValues": "1-2", "IsModifiable": true, "SettingName": "CHUNK_SIZE_GROWTH_FACTOR", "ApplyType": "STATIC"}, {"SettingDescription": "If enabled when there is no more memory to store items, memcached will return an error rather than evicting items.", "DefaultValue": "0", "AllowedValues": "0,1", "IsModifiable": true, "SettingName": "ERROR_ON_MEMORY_EXHAUSTED", "ApplyType": "STATIC"}, {"SettingDescription": "Maximum number of concurrent connections. Setting this value to anything less than 10 prevents MySQL from starting.", "DefaultValue": "1024", "AllowedValues": "10-1024", "IsModifiable": true, "SettingName": "MAX_SIMULTANEOUS_CONNECTIONS", "ApplyType": "STATIC"}, {"SettingDescription": "Verbose level for memcached.", "DefaultValue": "v", "AllowedValues": "v,vv,vvv", "IsModifiable": true, "SettingName": "VERBOSITY", "ApplyType": "STATIC"}], "EngineName": "mysql", "Name": "MEMCACHED", "PortRequired": true, "Description": "Innodb Memcached for MySQL"}]}, "ResponseMetadata": {"RequestId": "c9847a08-9fca-11e4-9084-5754f80d5144"}}}',
                      '5.6': '{"DescribeOptionGroupOptionsResponse": {"DescribeOptionGroupOptionsResult": {"Marker": null, "OptionGroupOptions": [{"MinimumRequiredMinorEngineVersion": "12", "OptionsDependedOn": [], "MajorEngineVersion": "5.6", "Persistent": false, "DefaultPort": 11211, "Permanent": false, "OptionGroupOptionSettings": [{"SettingDescription": "Specifies how many memcached read operations (get) to perform before doing a COMMIT to start a new transaction", "DefaultValue": "1", "AllowedValues": "1-4294967295", "IsModifiable": true, "SettingName": "DAEMON_MEMCACHED_R_BATCH_SIZE", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies how many memcached write operations, such as add, set, or incr, to perform before doing a COMMIT to start a new transaction", "DefaultValue": "1", "AllowedValues": "1-4294967295", "IsModifiable": true, "SettingName": "DAEMON_MEMCACHED_W_BATCH_SIZE", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies how often to auto-commit idle connections that use the InnoDB memcached interface.", "DefaultValue": "5", "AllowedValues": "1-1073741824", "IsModifiable": true, "SettingName": "INNODB_API_BK_COMMIT_INTERVAL", "ApplyType": "DYNAMIC"}, {"SettingDescription": "Disables the use of row locks when using the InnoDB memcached interface.", "DefaultValue": "0", "AllowedValues": "0,1", "IsModifiable": true, "SettingName": "INNODB_API_DISABLE_ROWLOCK", "ApplyType": "STATIC"}, {"SettingDescription": "Locks the table used by the InnoDB memcached plugin, so that it cannot be dropped or altered by DDL through the SQL interface.", "DefaultValue": "0", "AllowedValues": "0,1", "IsModifiable": true, "SettingName": "INNODB_API_ENABLE_MDL", "ApplyType": "STATIC"}, {"SettingDescription": "Lets you control the transaction isolation level on queries processed by the memcached interface.", "DefaultValue": "0", "AllowedValues": "0-3", "IsModifiable": true, "SettingName": "INNODB_API_TRX_LEVEL", "ApplyType": "STATIC"}, {"SettingDescription": "The binding protocol to use which can be either auto, ascii, or binary. The default is auto which means the server automatically negotiates the protocol with the client.", "DefaultValue": "auto", "AllowedValues": "auto,ascii,binary", "IsModifiable": true, "SettingName": "BINDING_PROTOCOL", "ApplyType": "STATIC"}, {"SettingDescription": "The backlog queue configures how many network connections can be waiting to be processed by memcached", "DefaultValue": "1024", "AllowedValues": "1-2048", "IsModifiable": true, "SettingName": "BACKLOG_QUEUE_LIMIT", "ApplyType": "STATIC"}, {"SettingDescription": "Disable the use of compare and swap (CAS) which reduces the per-item size by 8 bytes.", "DefaultValue": "0", "AllowedValues": "0,1", "IsModifiable": true, "SettingName": "CAS_DISABLED", "ApplyType": "STATIC"}, {"SettingDescription": "Minimum chunk size in bytes to allocate for the smallest item\'s key, value, and flags. The default is 48 and you can get a significant memory efficiency gain with a lower value.", "DefaultValue": "48", "AllowedValues": "1-48", "IsModifiable": true, "SettingName": "CHUNK_SIZE", "ApplyType": "STATIC"}, {"SettingDescription": "Chunk size growth factor that controls the size of each successive chunk with each chunk growing times this amount larger than the previous chunk.", "DefaultValue": "1.25", "AllowedValues": "1-2", "IsModifiable": true, "SettingName": "CHUNK_SIZE_GROWTH_FACTOR", "ApplyType": "STATIC"}, {"SettingDescription": "If enabled when there is no more memory to store items, memcached will return an error rather than evicting items.", "DefaultValue": "0", "AllowedValues": "0,1", "IsModifiable": true, "SettingName": "ERROR_ON_MEMORY_EXHAUSTED", "ApplyType": "STATIC"}, {"SettingDescription": "Maximum number of concurrent connections. Setting this value to anything less than 10 prevents MySQL from starting.", "DefaultValue": "1024", "AllowedValues": "10-1024", "IsModifiable": true, "SettingName": "MAX_SIMULTANEOUS_CONNECTIONS", "ApplyType": "STATIC"}, {"SettingDescription": "Verbose level for memcached.", "DefaultValue": "v", "AllowedValues": "v,vv,vvv", "IsModifiable": true, "SettingName": "VERBOSITY", "ApplyType": "STATIC"}], "EngineName": "mysql", "Name": "MEMCACHED", "PortRequired": true, "Description": "Innodb Memcached for MySQL"}]}, "ResponseMetadata": {"RequestId": "c9847a08-9fca-11e4-9084-5754f80d5144"}}}',
            },
            'sqlserver-ee': {'all': '{"DescribeOptionGroupOptionsResponse": {"DescribeOptionGroupOptionsResult": {"Marker": null, "OptionGroupOptions": [{"MinimumRequiredMinorEngineVersion": "2789.0.v1", "OptionsDependedOn": [], "MajorEngineVersion": "10.50", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "sqlserver-ee", "Name": "Mirroring", "PortRequired": false, "Description": "SQLServer Database Mirroring"}, {"MinimumRequiredMinorEngineVersion": "2789.0.v1", "OptionsDependedOn": [], "MajorEngineVersion": "10.50", "Persistent": true, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "sqlserver-ee", "Name": "TDE", "PortRequired": false, "Description": "SQL Server - Transparent Data Encryption"}, {"MinimumRequiredMinorEngineVersion": "2100.60.v1", "OptionsDependedOn": [], "MajorEngineVersion": "11.00", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "sqlserver-ee", "Name": "Mirroring", "PortRequired": false, "Description": "SQLServer Database Mirroring"}, {"MinimumRequiredMinorEngineVersion": "2100.60.v1", "OptionsDependedOn": [], "MajorEngineVersion": "11.00", "Persistent": true, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "sqlserver-ee", "Name": "TDE", "PortRequired": false, "Description": "SQL Server - Transparent Data Encryption"}]}, "ResponseMetadata": {"RequestId": "c9f2fd9b-9fcb-11e4-8add-31b6fe33145f"}}}',
                             '10.50': '{"DescribeOptionGroupOptionsResponse": {"DescribeOptionGroupOptionsResult": {"Marker": null, "OptionGroupOptions": [{"MinimumRequiredMinorEngineVersion": "2789.0.v1", "OptionsDependedOn": [], "MajorEngineVersion": "10.50", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "sqlserver-ee", "Name": "Mirroring", "PortRequired": false, "Description": "SQLServer Database Mirroring"}, {"MinimumRequiredMinorEngineVersion": "2789.0.v1", "OptionsDependedOn": [], "MajorEngineVersion": "10.50", "Persistent": true, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "sqlserver-ee", "Name": "TDE", "PortRequired": false, "Description": "SQL Server - Transparent Data Encryption"}]}, "ResponseMetadata": {"RequestId": "e6326fd0-9fcb-11e4-99cf-55e92d4bbada"}}}',
                             '11.00': '{"DescribeOptionGroupOptionsResponse": {"DescribeOptionGroupOptionsResult": {"Marker": null, "OptionGroupOptions": [{"MinimumRequiredMinorEngineVersion": "2100.60.v1", "OptionsDependedOn": [], "MajorEngineVersion": "11.00", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "sqlserver-ee", "Name": "Mirroring", "PortRequired": false, "Description": "SQLServer Database Mirroring"}, {"MinimumRequiredMinorEngineVersion": "2100.60.v1", "OptionsDependedOn": [], "MajorEngineVersion": "11.00", "Persistent": true, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "sqlserver-ee", "Name": "TDE", "PortRequired": false, "Description": "SQL Server - Transparent Data Encryption"}]}, "ResponseMetadata": {"RequestId": "222cbeeb-9fcc-11e4-bb07-576f5bf522b5"}}}'
            },
            'oracle-ee': {'all': '{"DescribeOptionGroupOptionsResponse": {"DescribeOptionGroupOptionsResult": {"Marker": null, "OptionGroupOptions": [{"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": ["XMLDB"], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "APEX", "PortRequired": false, "Description": "Oracle Application Express Runtime Environment"}, {"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": ["APEX"], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "APEX-DEV", "PortRequired": false, "Description": "Oracle Application Express Development Environment"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [{"SettingDescription": "Specifies the desired encryption behavior", "DefaultValue": "REQUESTED", "AllowedValues": "ACCEPTED,REJECTED,REQUESTED,REQUIRED", "IsModifiable": true, "SettingName": "SQLNET.ENCRYPTION_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies the desired data integrity behavior", "DefaultValue": "REQUESTED", "AllowedValues": "ACCEPTED,REJECTED,REQUESTED,REQUIRED", "IsModifiable": true, "SettingName": "SQLNET.CRYPTO_CHECKSUM_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies list of encryption algorithms in order of intended use", "DefaultValue": "RC4_256,AES256,AES192,3DES168,RC4_128,AES128,3DES112,RC4_56,DES,RC4_40,DES40", "AllowedValues": "RC4_256,AES256,AES192,3DES168,RC4_128,AES128,3DES112,RC4_56,DES,RC4_40,DES40", "IsModifiable": true, "SettingName": "SQLNET.ENCRYPTION_TYPES_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies list of checksumming algorithms in order of intended use", "DefaultValue": "SHA1,MD5", "AllowedValues": "SHA1,MD5", "IsModifiable": true, "SettingName": "SQLNET.CRYPTO_CHECKSUM_TYPES_SERVER", "ApplyType": "STATIC"}], "EngineName": "oracle-ee", "Name": "NATIVE_NETWORK_ENCRYPTION", "PortRequired": false, "Description": "Oracle Advanced Security - Native Network Encryption"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": 1158, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "OEM", "PortRequired": true, "Description": "Oracle Enterprise Manager (Database Control only)"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "STATSPACK", "PortRequired": false, "Description": "Oracle Statspack"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "TDE", "PortRequired": false, "Description": "Oracle Advanced Security - Transparent Data Encryption"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "TDE_HSM", "PortRequired": false, "Description": "Oracle Advanced Security - TDE with HSM"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [{"SettingDescription": "Specifies the timezone the user wants to change the system time to", "DefaultValue": "UTC", "AllowedValues": "Africa/Cairo,Africa/Casablanca,Africa/Harare,Africa/Monrovia,Africa/Nairobi,Africa/Tripoli,Africa/Windhoek,America/Araguaina,America/Asuncion,America/Bogota,America/Caracas,America/Chihuahua,America/Cuiaba,America/Denver,America/Fortaleza,America/Guatemala,America/Halifax,America/Manaus,America/Matamoros,America/Monterrey,America/Montevideo,America/Phoenix,America/Santiago,America/Tijuana,Asia/Amman,Asia/Ashgabat,Asia/Baghdad,Asia/Baku,Asia/Bangkok,Asia/Beirut,Asia/Calcutta,Asia/Damascus,Asia/Dhaka,Asia/Irkutsk,Asia/Jerusalem,Asia/Kabul,Asia/Karachi,Asia/Kathmandu,Asia/Krasnoyarsk,Asia/Magadan,Asia/Muscat,Asia/Novosibirsk,Asia/Riyadh,Asia/Seoul,Asia/Shanghai,Asia/Singapore,Asia/Taipei,Asia/Tehran,Asia/Tokyo,Asia/Ulaanbaatar,Asia/Vladivostok,Asia/Yakutsk,Asia/Yerevan,Atlantic/Azores,Australia/Adelaide,Australia/Brisbane,Australia/Darwin,Australia/Hobart,Australia/Perth,Australia/Sydney,Brazil/East,Canada/Newfoundland,Canada/Saskatchewan,Europe/Amsterdam,Europe/Athens,Europe/Dublin,Europe/Helsinki,Europe/Istanbul,Europe/Kaliningrad,Europe/Moscow,Europe/Paris,Europe/Prague,Europe/Sarajevo,Pacific/Auckland,Pacific/Fiji,Pacific/Guam,Pacific/Honolulu,Pacific/Samoa,US/Alaska,US/Central,US/Eastern,US/East-Indiana,US/Pacific,UTC", "IsModifiable": true, "SettingName": "TIME_ZONE", "ApplyType": "DYNAMIC"}], "EngineName": "oracle-ee", "Name": "Timezone", "PortRequired": false, "Description": "Change time zone"}, {"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "XMLDB", "PortRequired": false, "Description": "Oracle XMLDB Repository"}]}, "ResponseMetadata": {"RequestId": "36a0a612-9fcc-11e4-a07c-e12b0fcebb71"}}}',
                          '11.2': '{"DescribeOptionGroupOptionsResponse": {"DescribeOptionGroupOptionsResult": {"Marker": null, "OptionGroupOptions": [{"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": ["XMLDB"], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "APEX", "PortRequired": false, "Description": "Oracle Application Express Runtime Environment"}, {"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": ["APEX"], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "APEX-DEV", "PortRequired": false, "Description": "Oracle Application Express Development Environment"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [{"SettingDescription": "Specifies the desired encryption behavior", "DefaultValue": "REQUESTED", "AllowedValues": "ACCEPTED,REJECTED,REQUESTED,REQUIRED", "IsModifiable": true, "SettingName": "SQLNET.ENCRYPTION_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies the desired data integrity behavior", "DefaultValue": "REQUESTED", "AllowedValues": "ACCEPTED,REJECTED,REQUESTED,REQUIRED", "IsModifiable": true, "SettingName": "SQLNET.CRYPTO_CHECKSUM_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies list of encryption algorithms in order of intended use", "DefaultValue": "RC4_256,AES256,AES192,3DES168,RC4_128,AES128,3DES112,RC4_56,DES,RC4_40,DES40", "AllowedValues": "RC4_256,AES256,AES192,3DES168,RC4_128,AES128,3DES112,RC4_56,DES,RC4_40,DES40", "IsModifiable": true, "SettingName": "SQLNET.ENCRYPTION_TYPES_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies list of checksumming algorithms in order of intended use", "DefaultValue": "SHA1,MD5", "AllowedValues": "SHA1,MD5", "IsModifiable": true, "SettingName": "SQLNET.CRYPTO_CHECKSUM_TYPES_SERVER", "ApplyType": "STATIC"}], "EngineName": "oracle-ee", "Name": "NATIVE_NETWORK_ENCRYPTION", "PortRequired": false, "Description": "Oracle Advanced Security - Native Network Encryption"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": 1158, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "OEM", "PortRequired": true, "Description": "Oracle Enterprise Manager (Database Control only)"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "STATSPACK", "PortRequired": false, "Description": "Oracle Statspack"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "TDE", "PortRequired": false, "Description": "Oracle Advanced Security - Transparent Data Encryption"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "TDE_HSM", "PortRequired": false, "Description": "Oracle Advanced Security - TDE with HSM"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [{"SettingDescription": "Specifies the timezone the user wants to change the system time to", "DefaultValue": "UTC", "AllowedValues": "Africa/Cairo,Africa/Casablanca,Africa/Harare,Africa/Monrovia,Africa/Nairobi,Africa/Tripoli,Africa/Windhoek,America/Araguaina,America/Asuncion,America/Bogota,America/Caracas,America/Chihuahua,America/Cuiaba,America/Denver,America/Fortaleza,America/Guatemala,America/Halifax,America/Manaus,America/Matamoros,America/Monterrey,America/Montevideo,America/Phoenix,America/Santiago,America/Tijuana,Asia/Amman,Asia/Ashgabat,Asia/Baghdad,Asia/Baku,Asia/Bangkok,Asia/Beirut,Asia/Calcutta,Asia/Damascus,Asia/Dhaka,Asia/Irkutsk,Asia/Jerusalem,Asia/Kabul,Asia/Karachi,Asia/Kathmandu,Asia/Krasnoyarsk,Asia/Magadan,Asia/Muscat,Asia/Novosibirsk,Asia/Riyadh,Asia/Seoul,Asia/Shanghai,Asia/Singapore,Asia/Taipei,Asia/Tehran,Asia/Tokyo,Asia/Ulaanbaatar,Asia/Vladivostok,Asia/Yakutsk,Asia/Yerevan,Atlantic/Azores,Australia/Adelaide,Australia/Brisbane,Australia/Darwin,Australia/Hobart,Australia/Perth,Australia/Sydney,Brazil/East,Canada/Newfoundland,Canada/Saskatchewan,Europe/Amsterdam,Europe/Athens,Europe/Dublin,Europe/Helsinki,Europe/Istanbul,Europe/Kaliningrad,Europe/Moscow,Europe/Paris,Europe/Prague,Europe/Sarajevo,Pacific/Auckland,Pacific/Fiji,Pacific/Guam,Pacific/Honolulu,Pacific/Samoa,US/Alaska,US/Central,US/Eastern,US/East-Indiana,US/Pacific,UTC", "IsModifiable": true, "SettingName": "TIME_ZONE", "ApplyType": "DYNAMIC"}], "EngineName": "oracle-ee", "Name": "Timezone", "PortRequired": false, "Description": "Change time zone"}, {"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "XMLDB", "PortRequired": false, "Description": "Oracle XMLDB Repository"}]}, "ResponseMetadata": {"RequestId": "36a0a612-9fcc-11e4-a07c-e12b0fcebb71"}}}'
            },
            'oracle-sa': {'all': '{"DescribeOptionGroupOptionsResponse": {"DescribeOptionGroupOptionsResult": {"Marker": null, "OptionGroupOptions": [{"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": ["XMLDB"], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "APEX", "PortRequired": false, "Description": "Oracle Application Express Runtime Environment"}, {"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": ["APEX"], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "APEX-DEV", "PortRequired": false, "Description": "Oracle Application Express Development Environment"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [{"SettingDescription": "Specifies the desired encryption behavior", "DefaultValue": "REQUESTED", "AllowedValues": "ACCEPTED,REJECTED,REQUESTED,REQUIRED", "IsModifiable": true, "SettingName": "SQLNET.ENCRYPTION_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies the desired data integrity behavior", "DefaultValue": "REQUESTED", "AllowedValues": "ACCEPTED,REJECTED,REQUESTED,REQUIRED", "IsModifiable": true, "SettingName": "SQLNET.CRYPTO_CHECKSUM_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies list of encryption algorithms in order of intended use", "DefaultValue": "RC4_256,AES256,AES192,3DES168,RC4_128,AES128,3DES112,RC4_56,DES,RC4_40,DES40", "AllowedValues": "RC4_256,AES256,AES192,3DES168,RC4_128,AES128,3DES112,RC4_56,DES,RC4_40,DES40", "IsModifiable": true, "SettingName": "SQLNET.ENCRYPTION_TYPES_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies list of checksumming algorithms in order of intended use", "DefaultValue": "SHA1,MD5", "AllowedValues": "SHA1,MD5", "IsModifiable": true, "SettingName": "SQLNET.CRYPTO_CHECKSUM_TYPES_SERVER", "ApplyType": "STATIC"}], "EngineName": "oracle-ee", "Name": "NATIVE_NETWORK_ENCRYPTION", "PortRequired": false, "Description": "Oracle Advanced Security - Native Network Encryption"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": 1158, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "OEM", "PortRequired": true, "Description": "Oracle Enterprise Manager (Database Control only)"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "STATSPACK", "PortRequired": false, "Description": "Oracle Statspack"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "TDE", "PortRequired": false, "Description": "Oracle Advanced Security - Transparent Data Encryption"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "TDE_HSM", "PortRequired": false, "Description": "Oracle Advanced Security - TDE with HSM"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [{"SettingDescription": "Specifies the timezone the user wants to change the system time to", "DefaultValue": "UTC", "AllowedValues": "Africa/Cairo,Africa/Casablanca,Africa/Harare,Africa/Monrovia,Africa/Nairobi,Africa/Tripoli,Africa/Windhoek,America/Araguaina,America/Asuncion,America/Bogota,America/Caracas,America/Chihuahua,America/Cuiaba,America/Denver,America/Fortaleza,America/Guatemala,America/Halifax,America/Manaus,America/Matamoros,America/Monterrey,America/Montevideo,America/Phoenix,America/Santiago,America/Tijuana,Asia/Amman,Asia/Ashgabat,Asia/Baghdad,Asia/Baku,Asia/Bangkok,Asia/Beirut,Asia/Calcutta,Asia/Damascus,Asia/Dhaka,Asia/Irkutsk,Asia/Jerusalem,Asia/Kabul,Asia/Karachi,Asia/Kathmandu,Asia/Krasnoyarsk,Asia/Magadan,Asia/Muscat,Asia/Novosibirsk,Asia/Riyadh,Asia/Seoul,Asia/Shanghai,Asia/Singapore,Asia/Taipei,Asia/Tehran,Asia/Tokyo,Asia/Ulaanbaatar,Asia/Vladivostok,Asia/Yakutsk,Asia/Yerevan,Atlantic/Azores,Australia/Adelaide,Australia/Brisbane,Australia/Darwin,Australia/Hobart,Australia/Perth,Australia/Sydney,Brazil/East,Canada/Newfoundland,Canada/Saskatchewan,Europe/Amsterdam,Europe/Athens,Europe/Dublin,Europe/Helsinki,Europe/Istanbul,Europe/Kaliningrad,Europe/Moscow,Europe/Paris,Europe/Prague,Europe/Sarajevo,Pacific/Auckland,Pacific/Fiji,Pacific/Guam,Pacific/Honolulu,Pacific/Samoa,US/Alaska,US/Central,US/Eastern,US/East-Indiana,US/Pacific,UTC", "IsModifiable": true, "SettingName": "TIME_ZONE", "ApplyType": "DYNAMIC"}], "EngineName": "oracle-ee", "Name": "Timezone", "PortRequired": false, "Description": "Change time zone"}, {"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "XMLDB", "PortRequired": false, "Description": "Oracle XMLDB Repository"}]}, "ResponseMetadata": {"RequestId": "36a0a612-9fcc-11e4-a07c-e12b0fcebb71"}}}',
                          '11.2': '{"DescribeOptionGroupOptionsResponse": {"DescribeOptionGroupOptionsResult": {"Marker": null, "OptionGroupOptions": [{"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": ["XMLDB"], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "APEX", "PortRequired": false, "Description": "Oracle Application Express Runtime Environment"}, {"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": ["APEX"], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "APEX-DEV", "PortRequired": false, "Description": "Oracle Application Express Development Environment"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [{"SettingDescription": "Specifies the desired encryption behavior", "DefaultValue": "REQUESTED", "AllowedValues": "ACCEPTED,REJECTED,REQUESTED,REQUIRED", "IsModifiable": true, "SettingName": "SQLNET.ENCRYPTION_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies the desired data integrity behavior", "DefaultValue": "REQUESTED", "AllowedValues": "ACCEPTED,REJECTED,REQUESTED,REQUIRED", "IsModifiable": true, "SettingName": "SQLNET.CRYPTO_CHECKSUM_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies list of encryption algorithms in order of intended use", "DefaultValue": "RC4_256,AES256,AES192,3DES168,RC4_128,AES128,3DES112,RC4_56,DES,RC4_40,DES40", "AllowedValues": "RC4_256,AES256,AES192,3DES168,RC4_128,AES128,3DES112,RC4_56,DES,RC4_40,DES40", "IsModifiable": true, "SettingName": "SQLNET.ENCRYPTION_TYPES_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies list of checksumming algorithms in order of intended use", "DefaultValue": "SHA1,MD5", "AllowedValues": "SHA1,MD5", "IsModifiable": true, "SettingName": "SQLNET.CRYPTO_CHECKSUM_TYPES_SERVER", "ApplyType": "STATIC"}], "EngineName": "oracle-ee", "Name": "NATIVE_NETWORK_ENCRYPTION", "PortRequired": false, "Description": "Oracle Advanced Security - Native Network Encryption"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": 1158, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "OEM", "PortRequired": true, "Description": "Oracle Enterprise Manager (Database Control only)"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "STATSPACK", "PortRequired": false, "Description": "Oracle Statspack"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "TDE", "PortRequired": false, "Description": "Oracle Advanced Security - Transparent Data Encryption"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "TDE_HSM", "PortRequired": false, "Description": "Oracle Advanced Security - TDE with HSM"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [{"SettingDescription": "Specifies the timezone the user wants to change the system time to", "DefaultValue": "UTC", "AllowedValues": "Africa/Cairo,Africa/Casablanca,Africa/Harare,Africa/Monrovia,Africa/Nairobi,Africa/Tripoli,Africa/Windhoek,America/Araguaina,America/Asuncion,America/Bogota,America/Caracas,America/Chihuahua,America/Cuiaba,America/Denver,America/Fortaleza,America/Guatemala,America/Halifax,America/Manaus,America/Matamoros,America/Monterrey,America/Montevideo,America/Phoenix,America/Santiago,America/Tijuana,Asia/Amman,Asia/Ashgabat,Asia/Baghdad,Asia/Baku,Asia/Bangkok,Asia/Beirut,Asia/Calcutta,Asia/Damascus,Asia/Dhaka,Asia/Irkutsk,Asia/Jerusalem,Asia/Kabul,Asia/Karachi,Asia/Kathmandu,Asia/Krasnoyarsk,Asia/Magadan,Asia/Muscat,Asia/Novosibirsk,Asia/Riyadh,Asia/Seoul,Asia/Shanghai,Asia/Singapore,Asia/Taipei,Asia/Tehran,Asia/Tokyo,Asia/Ulaanbaatar,Asia/Vladivostok,Asia/Yakutsk,Asia/Yerevan,Atlantic/Azores,Australia/Adelaide,Australia/Brisbane,Australia/Darwin,Australia/Hobart,Australia/Perth,Australia/Sydney,Brazil/East,Canada/Newfoundland,Canada/Saskatchewan,Europe/Amsterdam,Europe/Athens,Europe/Dublin,Europe/Helsinki,Europe/Istanbul,Europe/Kaliningrad,Europe/Moscow,Europe/Paris,Europe/Prague,Europe/Sarajevo,Pacific/Auckland,Pacific/Fiji,Pacific/Guam,Pacific/Honolulu,Pacific/Samoa,US/Alaska,US/Central,US/Eastern,US/East-Indiana,US/Pacific,UTC", "IsModifiable": true, "SettingName": "TIME_ZONE", "ApplyType": "DYNAMIC"}], "EngineName": "oracle-ee", "Name": "Timezone", "PortRequired": false, "Description": "Change time zone"}, {"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "XMLDB", "PortRequired": false, "Description": "Oracle XMLDB Repository"}]}, "ResponseMetadata": {"RequestId": "36a0a612-9fcc-11e4-a07c-e12b0fcebb71"}}}'
            },
            'oracle-sa1': {'all': '{"DescribeOptionGroupOptionsResponse": {"DescribeOptionGroupOptionsResult": {"Marker": null, "OptionGroupOptions": [{"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": ["XMLDB"], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "APEX", "PortRequired": false, "Description": "Oracle Application Express Runtime Environment"}, {"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": ["APEX"], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "APEX-DEV", "PortRequired": false, "Description": "Oracle Application Express Development Environment"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [{"SettingDescription": "Specifies the desired encryption behavior", "DefaultValue": "REQUESTED", "AllowedValues": "ACCEPTED,REJECTED,REQUESTED,REQUIRED", "IsModifiable": true, "SettingName": "SQLNET.ENCRYPTION_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies the desired data integrity behavior", "DefaultValue": "REQUESTED", "AllowedValues": "ACCEPTED,REJECTED,REQUESTED,REQUIRED", "IsModifiable": true, "SettingName": "SQLNET.CRYPTO_CHECKSUM_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies list of encryption algorithms in order of intended use", "DefaultValue": "RC4_256,AES256,AES192,3DES168,RC4_128,AES128,3DES112,RC4_56,DES,RC4_40,DES40", "AllowedValues": "RC4_256,AES256,AES192,3DES168,RC4_128,AES128,3DES112,RC4_56,DES,RC4_40,DES40", "IsModifiable": true, "SettingName": "SQLNET.ENCRYPTION_TYPES_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies list of checksumming algorithms in order of intended use", "DefaultValue": "SHA1,MD5", "AllowedValues": "SHA1,MD5", "IsModifiable": true, "SettingName": "SQLNET.CRYPTO_CHECKSUM_TYPES_SERVER", "ApplyType": "STATIC"}], "EngineName": "oracle-ee", "Name": "NATIVE_NETWORK_ENCRYPTION", "PortRequired": false, "Description": "Oracle Advanced Security - Native Network Encryption"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": 1158, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "OEM", "PortRequired": true, "Description": "Oracle Enterprise Manager (Database Control only)"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "STATSPACK", "PortRequired": false, "Description": "Oracle Statspack"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "TDE", "PortRequired": false, "Description": "Oracle Advanced Security - Transparent Data Encryption"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "TDE_HSM", "PortRequired": false, "Description": "Oracle Advanced Security - TDE with HSM"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [{"SettingDescription": "Specifies the timezone the user wants to change the system time to", "DefaultValue": "UTC", "AllowedValues": "Africa/Cairo,Africa/Casablanca,Africa/Harare,Africa/Monrovia,Africa/Nairobi,Africa/Tripoli,Africa/Windhoek,America/Araguaina,America/Asuncion,America/Bogota,America/Caracas,America/Chihuahua,America/Cuiaba,America/Denver,America/Fortaleza,America/Guatemala,America/Halifax,America/Manaus,America/Matamoros,America/Monterrey,America/Montevideo,America/Phoenix,America/Santiago,America/Tijuana,Asia/Amman,Asia/Ashgabat,Asia/Baghdad,Asia/Baku,Asia/Bangkok,Asia/Beirut,Asia/Calcutta,Asia/Damascus,Asia/Dhaka,Asia/Irkutsk,Asia/Jerusalem,Asia/Kabul,Asia/Karachi,Asia/Kathmandu,Asia/Krasnoyarsk,Asia/Magadan,Asia/Muscat,Asia/Novosibirsk,Asia/Riyadh,Asia/Seoul,Asia/Shanghai,Asia/Singapore,Asia/Taipei,Asia/Tehran,Asia/Tokyo,Asia/Ulaanbaatar,Asia/Vladivostok,Asia/Yakutsk,Asia/Yerevan,Atlantic/Azores,Australia/Adelaide,Australia/Brisbane,Australia/Darwin,Australia/Hobart,Australia/Perth,Australia/Sydney,Brazil/East,Canada/Newfoundland,Canada/Saskatchewan,Europe/Amsterdam,Europe/Athens,Europe/Dublin,Europe/Helsinki,Europe/Istanbul,Europe/Kaliningrad,Europe/Moscow,Europe/Paris,Europe/Prague,Europe/Sarajevo,Pacific/Auckland,Pacific/Fiji,Pacific/Guam,Pacific/Honolulu,Pacific/Samoa,US/Alaska,US/Central,US/Eastern,US/East-Indiana,US/Pacific,UTC", "IsModifiable": true, "SettingName": "TIME_ZONE", "ApplyType": "DYNAMIC"}], "EngineName": "oracle-ee", "Name": "Timezone", "PortRequired": false, "Description": "Change time zone"}, {"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "XMLDB", "PortRequired": false, "Description": "Oracle XMLDB Repository"}]}, "ResponseMetadata": {"RequestId": "36a0a612-9fcc-11e4-a07c-e12b0fcebb71"}}}',
                          '11.2': '{"DescribeOptionGroupOptionsResponse": {"DescribeOptionGroupOptionsResult": {"Marker": null, "OptionGroupOptions": [{"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": ["XMLDB"], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "APEX", "PortRequired": false, "Description": "Oracle Application Express Runtime Environment"}, {"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": ["APEX"], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "APEX-DEV", "PortRequired": false, "Description": "Oracle Application Express Development Environment"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [{"SettingDescription": "Specifies the desired encryption behavior", "DefaultValue": "REQUESTED", "AllowedValues": "ACCEPTED,REJECTED,REQUESTED,REQUIRED", "IsModifiable": true, "SettingName": "SQLNET.ENCRYPTION_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies the desired data integrity behavior", "DefaultValue": "REQUESTED", "AllowedValues": "ACCEPTED,REJECTED,REQUESTED,REQUIRED", "IsModifiable": true, "SettingName": "SQLNET.CRYPTO_CHECKSUM_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies list of encryption algorithms in order of intended use", "DefaultValue": "RC4_256,AES256,AES192,3DES168,RC4_128,AES128,3DES112,RC4_56,DES,RC4_40,DES40", "AllowedValues": "RC4_256,AES256,AES192,3DES168,RC4_128,AES128,3DES112,RC4_56,DES,RC4_40,DES40", "IsModifiable": true, "SettingName": "SQLNET.ENCRYPTION_TYPES_SERVER", "ApplyType": "STATIC"}, {"SettingDescription": "Specifies list of checksumming algorithms in order of intended use", "DefaultValue": "SHA1,MD5", "AllowedValues": "SHA1,MD5", "IsModifiable": true, "SettingName": "SQLNET.CRYPTO_CHECKSUM_TYPES_SERVER", "ApplyType": "STATIC"}], "EngineName": "oracle-ee", "Name": "NATIVE_NETWORK_ENCRYPTION", "PortRequired": false, "Description": "Oracle Advanced Security - Native Network Encryption"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": 1158, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "OEM", "PortRequired": true, "Description": "Oracle Enterprise Manager (Database Control only)"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "STATSPACK", "PortRequired": false, "Description": "Oracle Statspack"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "TDE", "PortRequired": false, "Description": "Oracle Advanced Security - Transparent Data Encryption"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "TDE_HSM", "PortRequired": false, "Description": "Oracle Advanced Security - TDE with HSM"}, {"MinimumRequiredMinorEngineVersion": "0.2.v3", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": true, "DefaultPort": null, "Permanent": true, "OptionGroupOptionSettings": [{"SettingDescription": "Specifies the timezone the user wants to change the system time to", "DefaultValue": "UTC", "AllowedValues": "Africa/Cairo,Africa/Casablanca,Africa/Harare,Africa/Monrovia,Africa/Nairobi,Africa/Tripoli,Africa/Windhoek,America/Araguaina,America/Asuncion,America/Bogota,America/Caracas,America/Chihuahua,America/Cuiaba,America/Denver,America/Fortaleza,America/Guatemala,America/Halifax,America/Manaus,America/Matamoros,America/Monterrey,America/Montevideo,America/Phoenix,America/Santiago,America/Tijuana,Asia/Amman,Asia/Ashgabat,Asia/Baghdad,Asia/Baku,Asia/Bangkok,Asia/Beirut,Asia/Calcutta,Asia/Damascus,Asia/Dhaka,Asia/Irkutsk,Asia/Jerusalem,Asia/Kabul,Asia/Karachi,Asia/Kathmandu,Asia/Krasnoyarsk,Asia/Magadan,Asia/Muscat,Asia/Novosibirsk,Asia/Riyadh,Asia/Seoul,Asia/Shanghai,Asia/Singapore,Asia/Taipei,Asia/Tehran,Asia/Tokyo,Asia/Ulaanbaatar,Asia/Vladivostok,Asia/Yakutsk,Asia/Yerevan,Atlantic/Azores,Australia/Adelaide,Australia/Brisbane,Australia/Darwin,Australia/Hobart,Australia/Perth,Australia/Sydney,Brazil/East,Canada/Newfoundland,Canada/Saskatchewan,Europe/Amsterdam,Europe/Athens,Europe/Dublin,Europe/Helsinki,Europe/Istanbul,Europe/Kaliningrad,Europe/Moscow,Europe/Paris,Europe/Prague,Europe/Sarajevo,Pacific/Auckland,Pacific/Fiji,Pacific/Guam,Pacific/Honolulu,Pacific/Samoa,US/Alaska,US/Central,US/Eastern,US/East-Indiana,US/Pacific,UTC", "IsModifiable": true, "SettingName": "TIME_ZONE", "ApplyType": "DYNAMIC"}], "EngineName": "oracle-ee", "Name": "Timezone", "PortRequired": false, "Description": "Change time zone"}, {"MinimumRequiredMinorEngineVersion": "0.2.v4", "OptionsDependedOn": [], "MajorEngineVersion": "11.2", "Persistent": false, "DefaultPort": null, "Permanent": false, "OptionGroupOptionSettings": [], "EngineName": "oracle-ee", "Name": "XMLDB", "PortRequired": false, "Description": "Oracle XMLDB Repository"}]}, "ResponseMetadata": {"RequestId": "36a0a612-9fcc-11e4-a07c-e12b0fcebb71"}}}'
            }
        }
        if engine_name not in default_option_group_options:
            raise RDSClientError('InvalidParameterValue', 'Invalid DB engine: {}'.format(engine_name))
        if major_engine_version and major_engine_version not in default_option_group_options[engine_name]:
            raise RDSClientError('InvalidParameterCombination',
                                 'Cannot find major version {} for {}'.format(major_engine_version, engine_name))
        if major_engine_version:
            return default_option_group_options[engine_name][major_engine_version]
        return default_option_group_options[engine_name]['all']

    def modify_option_group(self, option_group_name, options_to_include=None, options_to_remove=None, apply_immediately=None):
        if option_group_name not in self.option_groups:
            raise RDSClientError('OptionGroupNotFoundFault',
                                 'Specified OptionGroupName: {} not found.'.format(option_group_name))
        if not options_to_include and not options_to_remove:
            raise RDSClientError('InvalidParameterValue',
                                 'At least one option must be added, modified, or removed.')
        if options_to_remove:
            self.option_groups[option_group_name].remove_options(options_to_remove)
        if options_to_include:
            self.option_groups[option_group_name].add_options(options_to_include)
        return self.option_groups[option_group_name]

    def list_tags_for_resource(self, arn):
        if self.arn_regex.match(arn):
            arn_breakdown = arn.split(':')
            resource_type = arn_breakdown[len(arn_breakdown)-2]
            resource_name = arn_breakdown[len(arn_breakdown)-1]
            if resource_type == 'db':  # Database
                if resource_name in self.databases:
                    return self.databases[resource_name].get_tags()
            elif resource_type == 'es':  # Event Subscription
                # TODO: Complete call to tags on resource type Event Subscription
                return []
            elif resource_type == 'og':  # Option Group
                if resource_name in self.option_groups:
                    return self.option_groups[resource_name].get_tags()
            elif resource_type == 'pg':  # Parameter Group
                # TODO: Complete call to tags on resource type Parameter Group
                return []
            elif resource_type == 'ri':  # Reserved DB instance
                # TODO: Complete call to tags on resource type Reserved DB instance
                return []
            elif resource_type == 'secgrp':  # DB security group
                if resource_type in self.security_groups:
                    return self.security_groups[resource_name].get_tags()
            elif resource_type == 'snapshot':  # DB Snapshot
                # TODO: Complete call to tags on resource type DB Snapshot
                return []
            elif resource_type == 'subgrp':  # DB subnet group
                if resource_type in self.subnet_groups:
                    return self.subnet_groups[resource_name].get_tags()
        else:
            raise RDSClientError('InvalidParameterValue',
                                 'Invalid resource name: {}'.format(arn))
        return []

    def remove_tags_from_resource(self, arn, tag_keys):
        if self.arn_regex.match(arn):
            arn_breakdown = arn.split(':')
            resource_type = arn_breakdown[len(arn_breakdown)-2]
            resource_name = arn_breakdown[len(arn_breakdown)-1]
            if resource_type == 'db':  # Database
                if resource_name in self.databases:
                    self.databases[resource_name].remove_tags(tag_keys)
            elif resource_type == 'es':  # Event Subscription
                return None
            elif resource_type == 'og':  # Option Group
                if resource_name in self.option_groups:
                    return self.option_groups[resource_name].remove_tags(tag_keys)
            elif resource_type == 'pg':  # Parameter Group
                return None
            elif resource_type == 'ri':  # Reserved DB instance
                return None
            elif resource_type == 'secgrp':  # DB security group
                if resource_type in self.security_groups:
                    return self.security_groups[resource_name].remove_tags(tag_keys)
            elif resource_type == 'snapshot':  # DB Snapshot
                return None
            elif resource_type == 'subgrp':  # DB subnet group
                if resource_type in self.subnet_groups:
                    return self.subnet_groups[resource_name].remove_tags(tag_keys)
        else:
            raise RDSClientError('InvalidParameterValue',
                                 'Invalid resource name: {}'.format(arn))

    def add_tags_to_resource(self, arn, tags):
        if self.arn_regex.match(arn):
            arn_breakdown = arn.split(':')
            resource_type = arn_breakdown[len(arn_breakdown)-2]
            resource_name = arn_breakdown[len(arn_breakdown)-1]
            if resource_type == 'db':  # Database
                if resource_name in self.databases:
                    return self.databases[resource_name].add_tags(tags)
            elif resource_type == 'es':  # Event Subscription
                return []
            elif resource_type == 'og':  # Option Group
                if resource_name in self.option_groups:
                    return self.option_groups[resource_name].add_tags(tags)
            elif resource_type == 'pg':  # Parameter Group
                return []
            elif resource_type == 'ri':  # Reserved DB instance
                return []
            elif resource_type == 'secgrp':  # DB security group
                if resource_type in self.security_groups:
                    return self.security_groups[resource_name].add_tags(tags)
            elif resource_type == 'snapshot':  # DB Snapshot
                return []
            elif resource_type == 'subgrp':  # DB subnet group
                if resource_type in self.subnet_groups:
                    return self.subnet_groups[resource_name].add_tags(tags)
        else:
            raise RDSClientError('InvalidParameterValue',
                                 'Invalid resource name: {}'.format(arn))


class OptionGroup(object):
    def __init__(self, name, engine_name, major_engine_version, description=None):
        self.engine_name = engine_name
        self.major_engine_version = major_engine_version
        self.description = description
        self.name = name
        self.vpc_and_non_vpc_instance_memberships = False
        self.options = {}
        self.vpcId = 'null'
        self.tags = []

    def to_json(self):
        template = Template("""{
    "VpcId": null,
    "MajorEngineVersion": "{{ option_group.major_engine_version }}",
    "OptionGroupDescription": "{{ option_group.description }}",
    "AllowsVpcAndNonVpcInstanceMemberships": "{{ option_group.vpc_and_non_vpc_instance_memberships }}",
    "EngineName": "{{ option_group.engine_name }}",
    "Options": [],
    "OptionGroupName": "{{ option_group.name }}"
}""")
        return template.render(option_group=self)

    def remove_options(self, options_to_remove):
        # TODO: Check for option in self.options and remove if exists. Raise error otherwise
        return

    def add_options(self, options_to_add):
        # TODO: Validate option and add it to self.options. If invalid raise error
        return

    def get_tags(self):
        return self.tags

    def add_tags(self, tags):
        new_keys = [tag_set['Key'] for tag_set in tags]
        self.tags = [tag_set for tag_set in self.tags if tag_set['Key'] not in new_keys]
        self.tags.extend(tags)
        return self.tags

    def remove_tags(self, tag_keys):
        self.tags = [tag_set for tag_set in self.tags if tag_set['Key'] not in tag_keys]


class OptionGroupOption(object):
    def __init__(self, engine_name, major_engine_version):
        self.engine_name = engine_name
        self.major_engine_version = major_engine_version
        #TODO: Create validation for Options
        #TODO: formulate way to store options settings

    def to_json(self):
        template = Template("""{ "MinimumRequiredMinorEngineVersion":
            "2789.0.v1",
            "OptionsDependedOn": [],
            "MajorEngineVersion": "10.50",
            "Persistent": false,
            "DefaultPort": null,
            "Permanent": false,
            "OptionGroupOptionSettings": [],
            "EngineName": "sqlserver-se",
            "Name": "Mirroring",
            "PortRequired": false,
            "Description": "SQLServer Database Mirroring"
        }""")
        return template.render(option_group=self)


rds2_backends = {}
for region in boto.rds2.regions():
    rds2_backends[region.name] = RDS2Backend()
