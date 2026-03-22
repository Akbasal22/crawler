# Production Deployment Recommendations

SQLite db should be transferred to a PostgreSQL db for better performance. In-memory queue should me moved to a message broker Redis. These will make the project scalable. Finally, containerize the application in Docker and deploy with Kubernetes into production environment. 

Next steps should include, enabling to search other websites than just Wikipedia. A roboparse should be implemented to better respect website policies. Also a limit on maximum number of requests made on a single website should be implemented to prevent harming the website. Automated backups and disaster recovery procedures should be implemented in case of crawling to a harmful website.





