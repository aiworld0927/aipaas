# syntax=docker/dockerfile:1
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /workspace
COPY pom.xml .
COPY libs ./libs
COPY apps ./apps
# --mount=type=cache 让 Maven 本地仓库在多次构建间复用，避免每次重新下载依赖
RUN --mount=type=cache,target=/root/.m2 mvn -q -pl apps/platform-management -am -DskipTests package

FROM eclipse-temurin:21-jre-alpine
WORKDIR /app
RUN addgroup -S aipaas && adduser -S aipaas -G aipaas \
 && mkdir -p /var/lib/aipaas/security /var/lib/aipaas/uploads /var/lib/aipaas/objects \
      /var/lib/aipaas/jwt-verify \
 && chown -R aipaas:aipaas /var/lib/aipaas
COPY --from=build /workspace/apps/platform-management/target/platform-management.jar /app/app.jar
USER aipaas
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=12 \
  CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-jar", "/app/app.jar"]
