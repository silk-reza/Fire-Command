from config.database import get_connection


connection = get_connection()

try:
    if connection.is_connected():
        print("Conexión a FireCommand exitosa.")

        cursor = connection.cursor()
        cursor.execute("SELECT VERSION()")

        version = cursor.fetchone()[0]
        print(f"MySQL: {version}")

        cursor.execute("SELECT COUNT(*) FROM incidentes")
        total_incidentes = cursor.fetchone()[0]

        print(f"Incidentes demo: {total_incidentes}")

        cursor.close()

finally:
    if connection.is_connected():
        connection.close()
        print("Conexión cerrada.")