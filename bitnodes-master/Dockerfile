# Use an official Python runtime as a parent image
FROM python:2.7-stretch

# Set the working directory to /app
WORKDIR /bitnodes

# Copy the current directory contents into the container at /app
COPY . /bitnodes

# Install any needed packages specified in requirements.txt
RUN pip install --trusted-host pypi.python.org -r requirements.txt

RUN echo "dicl15.cut.ac.cy" >> /etc/hostname

RUN echo  "127.0.0.1 localhost" >> /etc/hosts
#RUN echo "[IP_ADDRESS] [HOSTNAME]" >> /etc/hosts
RUN echo "::1 ip6-localhost ip6-loopback" >> /etc/hosts
RUN echo "fe00::0 ip6-localnet" >> /etc/hosts
RUN echo "ff00::0 ip6-mcastprefix" >> /etc/hosts
RUN echo "ff02::1 ip6-allnodes" >> /etc/hosts
RUN echo "ff02::2 ip6-allrouters" >> /etc/hosts
RUN echo "ff02::3 ip6-allhosts" >> /etc/hosts
#RUN echo "[IPV6_ADDRESS] [HOSTNAME]" >> /etc/hosts


RUN geoip/update.sh


# Make port 80 available to the world outside this container
# EXPOSE 80

# Define environment variable
# ENV NAME World



# Run app.py when the container launches
# CMD ["python", "app.py"]
# CMD ["program", "arg1", "arg2"]

#CMD ./start.sh
CMD ["/bin/sh", "./start.sh"]