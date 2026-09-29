from setuptools import setup

package_name = 'deskpet_relay'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Liu Zhili',
    maintainer_email='liuzhili86@gmail.com',
    description='WebSocket relay bridging Primitive action to non-ROS clients',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'relay_server = deskpet_relay.relay_server:main',
        ],
    },
)
