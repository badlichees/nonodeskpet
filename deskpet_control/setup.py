from setuptools import setup

package_name = 'deskpet_control'

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
    description='Semantic action primitives to cmd_vel for the deskpet project',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'primitive_server = deskpet_control.primitive_server:main',
        ],
    },
)
